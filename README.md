# Music Analyzer

[![CI](https://github.com/Majkey25/music-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/Majkey25/music-analyzer/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Majkey25/music-analyzer/actions/workflows/codeql.yml/badge.svg)](https://github.com/Majkey25/music-analyzer/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/Majkey25/music-analyzer)](https://github.com/Majkey25/music-analyzer/releases/latest)
[![Proprietary](https://img.shields.io/badge/license-proprietary-555)](LICENSE)

Přepiš skladbu po stopách a cvič s vlastní tabulaturou. Music Analyzer běží v prohlížeči na tvém počítači. Přehraje originální skladbu, samotný part i doprovod bez něj.

![Přehrávač, basová tabulatura a nastavení vlastního ladění](docs/screenshots/tablature.png)

## Spuštění

1. Stáhni ZIP z [posledního vydání](https://github.com/Majkey25/music-analyzer/releases/latest) a rozbal jej.
2. Nainstaluj [uv](https://docs.astral.sh/uv/getting-started/installation/) a [FFmpeg](https://ffmpeg.org/download.html). Příkaz `ffmpeg -version` musí fungovat z nového terminálu.
3. Ve Windows spusť `Spustit.cmd`. Na Linuxu nebo macOS spusť v kořeni projektu:

   ```sh
   uv run --locked --no-dev --extra separation python -X utf8 .
   ```

Aplikace otevře prohlížeč na `127.0.0.1`. Ukončíš ji pomocí Ctrl+C v terminálu. První instalace stáhne Python 3.12 a knihovny. Při první separaci se stáhnou také váhy modelu. Pak lze se stejnou instalací a uloženými váhami pracovat offline. Počítej s několika GB na disku a několika minutami na separaci delší skladby na CPU.

Projekt má zamčené závislosti pro Windows x64, Linux x64 a macOS Apple Silicon. Ověřený je Windows x64. Pokud potřebuješ jen MIDI nebo přepis izolované stopy, můžeš vynechat `--extra separation`.

## Práce s přepisem

* **Celá skladba** | Načti WAV, MP3 nebo FLAC. Použij **Oddělit stopy**, vyber stopu a spusť **Přepsat stopu**. Model nabízí basu, bicí, kytaru, klavír, zpěv a ostatní zvuk. Izolace nástrojů závisí na mixu.
* **Izolovaný nástroj** | Vyber typ nástroje a spusť přepis přímo. Pro housle je připravený vlastní rozsah. Pro dechové a další tónové nástroje použij **Jiný melodický nástroj**. Pro kopák, virbl, hi-hat a tomy použij **Bicí**.
* **MIDI** | Načti MIDI typu 0 nebo 1 a vyber jeho stopu. Můžeš současně přidat zvuk a přehrávat ho jako referenci. **Zarovnání not** upraví čas a transpozici. MIDI se importuje v původním čase včetně počátečního ticha.
* **Ladění** | Vepiš otevřené struny od nejhlubší, včetně oktáv, například `F0 C1 F1 Bb1`. Počet položek určuje počet strun. Nastav skutečný počet pražců. V editoru můžeš každé notě vybrat jinou hratelnou strunu.
* **Rytmus** | Import MIDI použije jeho tempo a změny taktu. U audia dostaneš odhad tempa a první doby. Takt a začátek první doby můžeš opravit. Číslo `3.1` v přepisu označuje první dobu třetího taktu.
* **Partitura** | Tabulatura má pražce, struny, pomlky a rytmické hodnoty. U klavíru je k dispozici dvojitá osnova, u bicích perkusní osnova. V piano rollu jsou přesné nástupy a délky not, u klavíru i klávesy. Klikni na notu nebo takt a poslechni si dané místo. Takt můžeš vybrat i pomocí Tab a Enter.
* **Export** | MIDI zachovává čas přepisu a mapu tempa a taktů. MusicXML zapisuje noty, pomlky, akordy a vazby do šestnáctinové mřížky. Pokud máš otevřenou tabulaturu, přidá i ladění, struny a pražce. Formát TAB .txt obsahuje pražce, časy a doby.

## Trénink a uložení

1. Vyber part, který chceš číst. **Celá skladba** přehraje mix. **Sólo stopy** přehraje vybraný part. **Bez této stopy** složí doprovod ze zbývajících oddělených stop.
2. V nabídce **Vlastní doprovod** si vyber přesnou kombinaci stop. Poslech můžeš zpomalit nebo spustit mezerníkem. **Smyčka** opakuje aktuální takt. Kliknutím na jiný takt přesuneš smyčku.
3. **Uložit relaci** stáhne soubor `.music-analyzer.zip` se zvukem, zvolenou stopou, úpravami not, laděním, rytmickou mřížkou a nastavením poslechu. Otevři jej přímo přes **Otevřít soubor**. Soubor si ponech celý, aby zůstal přehratelný i na jiném počítači.

![Dvojitá klavírní osnova a volba poslechu](docs/screenshots/piano.png)

<details>
<summary>Mobilní zobrazení</summary>

![Klavírní osnova a ovládání poslechu v mobilním prohlížeči](docs/screenshots/mobile.png)

</details>

## Přesnost a soukromí

Přepis audia je odhad. Zkreslení, krátké noty, ohýbané tóny a husté souzvuky mohou vyžadovat ruční úpravu. Režim **Basa** sleduje jednu basovou linku včetně hlubokých ladění. Přepis bicích odhaduje kopák, virbl, hi-hat a tomy podle nástupu a spektra. Činely a souběžné údery může sloučit. Stejný tón lze zahrát na více strunách a výsledný prstoklad můžeš upravit. Housle z plného mixu mohou zůstat ve stopě **Ostatní**.

Zvuk se zpracovává na tomto počítači. Aplikace nemá účet ani vzdálené úložiště. Zdrojové nahrávky nemění. Pracovní soubory drží v dočasné místní složce do zavření relace nebo ukončení aplikace. Po násilném ukončení může dočasná složka zůstat na disku. Jedna relace přijímá nejvýš 256 MB na import, 15 minut audia a 20 000 not na stopu.

## Vývoj a ověření

```sh
uv sync --locked
uv run ruff check
uv run pyright
uv run python -m unittest discover -s tests
uv run python -m tests.model_checks
uv run playwright install chromium
uv run python -m tests.browser_checks
```

Základní testy ověřují MIDI časování a sustain, rytmus, přiřazení strun, bicí a skutečné vykreslení not. Při testování UI použij **Studiová ukázka**, která obsahuje syntetické basové, klavírní a bicí party bez externích souborů.

Separaci zajišťuje [HTDemucs 6s](https://github.com/facebookresearch/demucs) přes [demucs-infer](https://github.com/openmirlab/demucs-infer). Přepis tónových nástrojů používá [Spotify Basic Pitch](https://github.com/spotify/basic-pitch) s ONNX Runtime, basa a bicí mají samostatné analyzátory. Partitury vykresluje [Verovio](https://www.verovio.org/). Tempo a polohu dob odhaduje [librosa](https://librosa.org/).

## Licence

Zdrojový kód je veřejně dostupný pro osobní použití podle [proprietární licence](LICENSE). Na knihovny, nástroje a váhy modelů se vztahují [jejich vlastní podmínky](THIRD_PARTY.md). Historie vydání je v [CHANGELOG.md](CHANGELOG.md).
