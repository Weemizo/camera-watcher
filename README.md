# Camera watcher: alerty OLX → Telegram

Śledzi na OLX (kategoria „Aparaty kompaktowe”) Sony DSC-WX350 i lepsze kompakty: WX500, HX80/90/95/99, RX100, Canon SX720–740, G7 X, G9 X, Lumix TZ70–TZ200.
Gdy cena wynosi ≤ 500 zł (RX100 ≤ 600 zł, oferty „do negocjacji” +50 zł), wysyła na telefon wiadomość ze zdjęciem i przyciskiem **Otwórz na OLX**.

Wszystko jest darmowe: publiczne API OLX i Telegram Bot API. Nie ma kluczy, kredytów ani dodatkowych bibliotek (wystarczy sam Python).

## 1. Bot Telegram (2 min)
1. W Telegramie otwórz **@BotFather** → `/newbot` → podaj nazwę → skopiuj **token**.
2. Napisz cokolwiek do swojego nowego bota.
3. Utwórz plik `.env` (wzór: `.env.example`), wklej token i uruchom `python watcher.py --chat-id`, żeby dostać swoje `TELEGRAM_CHAT_ID`.
4. Uruchom `python watcher.py --test`. Na telefon powinna przyjść wiadomość „✅ Bot działa”.

## 2a. Uruchamianie na własnym PC
```
python watcher.py --loop 600
```
Sprawdza co 10 min, ale działa tylko wtedy, gdy komputer jest włączony.

Uruchamiaj to z **Git Bash**, a nie z PowerShella. OLX blokuje (błąd 403) curl wbudowany w Windows, a przepuszcza curl z Gita. Wbudowany curl Pythona (`urllib`) też jest blokowany, dlatego skrypt pobiera dane przez curl. Na serwerach GitHuba (Linux) curl jest standardowy, więc nic nie trzeba robić.

## 2b. GitHub Actions (działa 24/7, za darmo)
1. Załóż **publiczne** repozytorium na GitHubie i wrzuć do niego ten folder. W repozytoriach publicznych minuty Actions są bez limitu, a token i tak trafia do zaszyfrowanych sekretów.
2. Otwórz Settings → Secrets and variables → Actions i dodaj `TELEGRAM_BOT_TOKEN` oraz `TELEGRAM_CHAT_ID`.
3. W zakładce Actions otwórz **camera-watch** i kliknij **Run workflow**. Potem sprawdzanie będzie się uruchamiać samo co ~15 min.

## Konfiguracja (`config.json`)
- `max_price` / `min_price`: przedział cen. `min_price` odsiewa ogłoszenia za 1 zł i części.
- `models`: dodaj lub usuń modele. `pattern` to wyrażenie regularne sprawdzane w tytule (OLX szuka bardzo luźno, np. „wx350” zwraca też Lexusa NX350).
- `exclude_title_keywords`: odrzuca ogłoszenia z tymi słowami („części”, „uszkodzony”, „kupię”…).

- `stuffing_markers`: sprzedawcy dopisują modele, których nie sprzedają („Olympus … y2k jak g7x”). Model jest szukany tylko w części tytułu przed tymi słowami.

Alert przychodzi raz na ofertę. Drugi przychodzi tylko wtedy, gdy sprzedawca obniży cenę (📉).

## Uwaga
To nieoficjalne API strony OLX. Jeśli OLX zaostrzy blokadę, w logach Actions zobaczysz błędy zapytań i alerty przestaną przychodzić. Na awaryjną sytuację w aplikacji OLX można włączyć „Obserwuj wyszukiwanie” z filtrem ceny.
