"""Build a relocatable Windows package without copying personal desktop data."""
from __future__ import annotations
import argparse
import copy
from datetime import date, datetime
import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent


def write_text(path, text, *, bom=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8-sig' if bom else 'utf-8')


def prepare(build_root: Path) -> Path:
    from config_mgr import DEFAULT_CONFIG
    package = build_root / 'dist' / 'GameDock'
    if not (package / 'GameDock.exe').is_file():
        raise RuntimeError('First build GameDock.exe with PyInstaller.')
    shutil.copytree(ROOT / 'assets', package / 'assets', dirs_exist_ok=True)
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(autostart=False, dock_x=None, dock_y=None)
    write_text(package / 'config.json', json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    (package / 'Shortcuts').mkdir(exist_ok=True)
    for template in (ROOT / 'distribution').iterdir():
        if template.suffix in {'.ps1', '.txt'}:
            write_text(package / template.name, template.read_text(encoding='utf-8-sig'), bom=True)
        elif template.suffix == '.cmd':
            text = template.read_text(encoding='utf-8-sig').replace('\r\n', '\n').replace('\n', '\r\n')
            (package / template.name).write_bytes(text.encode('ascii'))
    write_text(package / 'PRZECZYTAJ_NAJPIERW.txt',
               'GAMEDOCK\n\n1. Wypakuj CAŁY ZIP.\n2. Otwórz folder GameDock.\n'
               '3. Uruchom Zainstaluj.cmd.\n\nMożesz też uruchomić GameDock.exe bez instalacji.\n'
               'Nie potrzebujesz Pythona ani Internetu do instalacji GameDock.\n'
               'Wymagany Windows 11 x64 (Intel/AMD). Gry instalujesz osobno.\n\n'
               'Pełna instrukcja: Instrukcja.txt.\nDiagnostyka: Sprawdz.cmd.\n', bom=True)
    license_root = package / 'licenses'
    license_root.mkdir(exist_ok=True)
    shutil.copy2(Path(sys.base_prefix) / 'LICENSE.txt', license_root / 'Python-LICENSE.txt')
    for name in ('PySide6', 'PySide6_Essentials', 'shiboken6', 'Pillow', 'pywin32', 'PyInstaller'):
        installed = distribution(name)
        for entry in installed.files or []:
            if not any(part.lower().startswith(('license', 'copying')) for part in entry.parts):
                continue
            destination = license_root / name / str(entry).replace('../', '')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(installed.locate_file(entry), destination)
    # Include the matching upstream sources and their attribution/license files.
    repositories = {
        'qtbase': ('qt/qtbase', 'v6.11.2'),
        'qtsvg': ('qt/qtsvg', 'v6.11.2'),
        'qtimageformats': ('qt/qtimageformats', 'v6.11.2'),
        'pyside-setup': ('qtproject/pyside-pyside-setup', 'v6.11.2'),
    }
    cache = ROOT / 'build' / 'library-sources'
    cache.mkdir(parents=True, exist_ok=True)
    for name, (repository, tag) in repositories.items():
        archive = cache / f'{name}-6.11.2.tar.gz'
        if not archive.is_file():
            url = f'https://codeload.github.com/{repository}/tar.gz/refs/tags/{tag}'
            print(f'Downloading source: {name}', flush=True)
            request = urllib.request.Request(url, headers={'User-Agent': 'GameDock-release-builder'})
            with urllib.request.urlopen(request, timeout=60) as response, archive.open('wb') as output:
                shutil.copyfileobj(response, output)
        target = license_root / 'sources' / archive.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, target)
        with tarfile.open(archive, 'r:gz') as source:
            for member in source:
                if not member.isfile():
                    continue
                relative = Path(*Path(member.name).parts[1:])
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Invalid upstream source path')
                if (any(part.lower() == 'licenses' for part in relative.parts)
                        or relative.name.lower().startswith(('license', 'copying', 'copyright'))
                        or relative.name == 'qt_attribution.json'):
                    destination = license_root / name / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(source.extractfile(member).read())
    with zipfile.ZipFile(package / 'Zrodla_GameDock.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as sources:
        files = list(ROOT.glob('*.py')) + list(ROOT.glob('requirements*.txt'))
        files += [ROOT / 'README.md', ROOT / 'setup.ps1', ROOT / 'GameDock.vbs']
        files += list((ROOT / 'tests').glob('*.py'))
        files += [path for path in (ROOT / 'assets').rglob('*') if path.is_file()]
        files += [path for path in (ROOT / 'distribution').rglob('*') if path.is_file()]
        for path in files:
            sources.write(path, 'GameDock-source/' + path.relative_to(ROOT).as_posix())
        sources.write(package / 'config.json', 'GameDock-source/config.json')
    files = [path for path in package.rglob('*') if path.is_file()
             and 'logs' not in path.relative_to(package).parts
             and path.name != 'package-manifest.json']
    manifest = {
        'version': date.today().isoformat(), 'platform': 'Windows 11 x64',
        'files': sorted(path.relative_to(package).as_posix() for path in files),
        'sha256': {path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
    }
    write_text(package / 'package-manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return package


def archive_package(package: Path) -> Path:
    release_root = ROOT / 'releases'
    release_root.mkdir(exist_ok=True)
    target = release_root / f'GameDock-Windows11-x64-{date.today().isoformat()}.zip'
    temporary = target.with_suffix('.building.zip')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(package.rglob('*')):
            if path.is_file() and 'logs' not in path.relative_to(package).parts:
                archive.write(path, 'GameDock/' + path.relative_to(package).as_posix())
        archive.writestr('GameDock/Shortcuts/', '')
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('ZIP verification failed')
    temporary.replace(target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    write_text(target.with_suffix('.sha256.txt'), f'{digest}  {target.name}\n')
    print(f'ZIP: {target}\nSize: {target.stat().st_size / (1024*1024):.1f} MB\nSHA256: {digest}', flush=True)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--existing-build', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--archive-only', action='store_true')
    args = parser.parse_args()
    build_root = (args.existing_build or ROOT / 'build' / datetime.now().strftime('release-%Y%m%d-%H%M%S')).resolve()
    if not build_root.is_relative_to(ROOT / 'build'):
        raise ValueError('Build output must stay inside the project build directory')
    if args.archive_only:
        archive_package(build_root / 'dist' / 'GameDock')
        return
    if not args.existing_build:
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
                        '--distpath', str(build_root / 'dist'), '--workpath', str(build_root / 'work'),
                        str(ROOT / 'distribution' / 'GameDock.spec')], check=True, cwd=ROOT)
    package = prepare(build_root)
    if not args.prepare_only:
        subprocess.run([str(package / 'GameDock.exe'), '--verify-package'], check=True, cwd=package)
        archive_package(package)


if __name__ == '__main__':
    main()
