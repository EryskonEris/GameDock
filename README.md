# GameDock

Szklany panel **Gry**: automatycznie rozpoznaje skróty gier na pulpicie Windows
i aktualizuje listę w tle co sekundę. Programy, instalatory i ikony systemowe
nie pojawiają się w panelu.

## Uruchamianie

Gotowa paczka na drugi komputer: `releases/GameDock-Windows11-x64-2026-10-07.zip`.
Wypakuj cały ZIP i uruchom `Zainstaluj.cmd` albo bezpośrednio `GameDock.exe`.
Ta wersja zawiera własne środowisko; nie wymaga instalowania Pythona.
Pełna instrukcja jest w paczce jako `Instrukcja.txt` oraz w `distribution/Instrukcja.txt`.

Poniższe kroki dotyczą uruchamiania z kodu źródłowego:
Wymagany Windows 11 oraz Python 3.10–3.14 (na tym komputerze: Python 3.14).
Uruchom `setup.ps1`, a następnie otwórz `GameDock.vbs`.
Launcher używa lokalnego środowiska `.venv` i nie zależy od ścieżki instalacji Pythona.
Ponowne otwarcie launchera pokazuje już działającą instancję.
Autostart można włączyć lub wyłączyć w ustawieniach panelu.

## Korzystanie

- Kliknij przycisk **Gry** lub ikonę GameDock obok zegara.
- Jedno kliknięcie uruchamia grę przez jej oryginalny skrót i chowa panel
  po poprawnym przekazaniu uruchomienia do Windows.
- Główny widok pokazuje wyłącznie ikony i nazwy gier.
- Pasek przewijania jest ukryty. Większą kolekcję przewijaj kółkiem myszy
  albo klawiszami strzałek, `Page Up` i `Page Down`.
- `Ctrl+F` pokazuje wyszukiwanie. Prawy przycisk na tle lub nagłówku udostępnia
  ulubione, sortowanie, widok listy i ustawienia.
- **Dodaj grę…** w tym menu pozwala wskazać istniejący skrót lub plik `.exe`,
  również spoza pulpitu. Oryginał pozostaje w swojej lokalizacji.
- **Usuń z listy gier** chowa pozycję w GameDock; pliki gry i jej skrót zostają
  na dysku. Ponowne **Dodaj grę…** przywraca pozycję. W ustawieniach można też
  przywrócić wszystkie gry usunięte z listy.
- **Zaznacz** w menu włącza wybieranie wielu gier; prawy przycisk na ikonie
  otwiera menu działań. Operacje na samym skrócie są w podmenu
  **Zarządzaj skrótem** i korzystają z mechanizmów Windows.
- Folder `Shortcuts` można włączyć jako dodatkowe źródło skrótów gier w ustawieniach.
- `Ctrl+F`: wyszukiwanie; strzałki: nawigacja; `Enter`: otwarcie;
  `Space`: zaznaczenie; `F2`: zmiana nazwy skrótu; `Delete`: usuń z listy;
  `F5`: odświeżenie;
  `Escape`: wyjście z zaznaczania lub schowanie panelu.

Panel korzysta z przezroczystej warstwy Qt w Windows: bezbarwne szkło lekko
przyciemnia tło (28%), a ikony i tekst pozostają w pełni czytelne. Tapeta i okna za panelem są
widoczne przez tło. Ustawienia są w `config.json`,
a dziennik błędów w `logs/gamedock.log`.

## Sprawdzanie

Uruchom `.venv\Scripts\python.exe -m unittest discover -s tests -v`.
Testy operacji plikowych używają wyłącznie danych tymczasowych.
`main.py --preview --no-dock` otwiera panel z tymczasowymi ustawieniami,
bez zmiany autostartu i bez drugiego przycisku na pulpicie.

## Budowanie paczki EXE

Na Windows x64, w środowisku z zainstalowanymi `requirements.txt`:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.venv\Scripts\python.exe -m unittest discover -s tests -q
.venv\Scripts\python.exe build_release.py
```

Budowanie wymaga Internetu do pobrania źródeł bibliotek Qt/PySide6.
Gotowa instalacja działa bez pobierania zależności.
Skrypt dołącza czyste ustawienia, instalator, instrukcję, źródła GameDock,
licencje i odpowiadające wersji źródła bibliotek Qt/PySide6. Nie kopiuje
osobistego `config.json`, gier, skrótów użytkownika ani dzienników.
Przed utworzeniem ZIP-a uruchamia diagnostykę gotowego EXE.
Archiwum i sumę SHA-256 zapisuje w `releases`.
