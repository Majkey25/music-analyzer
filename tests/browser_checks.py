"""Run with Playwright Chromium installed; all fixtures are synthetic."""

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

import mido
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from studio.server import create_app


def run() -> None:
    with TemporaryDirectory() as root, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        app = create_app(Path(root))
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("""(() => {
                window.scheduledClicks = [];
                const connect = AudioNode.prototype.connect;
                AudioNode.prototype.connect = function(target, ...ports) {
                    if (this instanceof GainNode && target instanceof AudioDestinationNode) window.clickGain = this;
                    return connect.call(this, target, ...ports);
                };
                const start = OscillatorNode.prototype.start;
                OscillatorNode.prototype.start = function(at) {
                    if ([1340, 935].includes(this.frequency.value)) window.scheduledClicks.push(at);
                    return start.call(this, at);
                };
            })();""")
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            markers = page.evaluate("""async () => {
                const { beatGrid } = await import('/assets/grid.js');
                return beatGrid([{time: 0, bpm: 120}], [
                    {time: 0, numerator: 4, denominator: 4}, {time: 2, numerator: 3, denominator: 4}
                ], 0, 4).flatMap(page => page.ticks).filter(tick => tick.beat === 1);
            }""")
            assert [(tick["bar"], tick["time"]) for tick in markers] == [(1, 0), (2, 2), (3, 3.5)]
            page.get_by_role("button", name="Studio demo").click()
            page.locator("#studio").wait_for(state="visible")
            expect(page.locator("#score-image g.note").first).to_be_visible()
            expect(page.locator("#score-image g.is-current .score-playhead")).to_have_count(1)
            expect(page.locator("#score-image g.beam polygon")).to_have_count(12)
            theme = page.get_by_role("combobox", name="Theme")
            theme.select_option("dark")
            expect(page.locator("#score-image")).to_have_css("background-color", "rgb(25, 30, 38)")
            theme.select_option("light")
            expect(page.locator("#score-image")).to_have_css("background-color", "rgb(255, 255, 255)")
            expect(page.locator("#follow")).to_be_checked()
            expect(page.locator("#count-in")).to_be_checked()
            expect(page.locator("#metronome")).not_to_be_checked()
            page.locator("#count-in").uncheck()
            page.locator("#follow").uncheck()
            volume = page.get_by_role("slider", name="Track volume")
            metronome_volume = page.get_by_role("slider", name="Metronome volume")
            expect(metronome_volume).to_have_value("60")
            volume.press("Home")
            assert page.locator("#player").evaluate("player => player.volume") == 0
            volume.press("End")
            page.get_by_role("button", name="Piano roll").click()
            expect(page.locator(".ruler-beat").first).to_have_text("1.1")
            page.get_by_text("Rhythm", exact=True).click()
            page.locator("#numerator").fill("3")
            page.locator("#rhythm-form button[type=submit]").click()
            expect(page.locator(".ruler-beat").nth(3)).to_have_text("2.1")
            page.get_by_role("button", name="Play", exact=True).click()
            page.wait_for_function("() => document.querySelector('#seek').valueAsNumber > .1")
            page.get_by_role("button", name="Pause", exact=True).click()
            page.get_by_role("button", name="Tablature").click()
            page.locator("#tuning").fill("F0 C1 F1 Bb1 D2")
            page.get_by_role("button", name="Apply tuning").click()
            expect(page.locator("#placement")).to_contain_text("5 strings")
            page.locator("#edit summary").click()
            first = page.locator("#note-table select").first
            first.select_option("0")
            expect(first).to_have_value("0")
            page.get_by_role("combobox", name="Note head for note at 0:00.00").select_option("ghost")
            expect(page.locator("#score-image #n0-0 text")).to_have_text("(0)")
            with page.expect_download() as tab:
                page.get_by_role("button", name="TAB .txt").click()
            assert tab.value.suggested_filename == "transcription.txt"
            page.locator("#loop").click()
            page.locator("#loop-from").fill("2")
            page.locator("#loop-through").fill("3")
            page.get_by_role("button", name="Set loop").click()
            expect(page.locator("#score-image .is-looped")).to_have_count(2)
            page.locator("#metronome").check()
            page.locator("#count-in").check()
            page.locator("#play").click()
            expect(page.get_by_role("button", name="Cancel count-in")).to_have_text("Counting…")
            page.wait_for_function("() => window.scheduledClicks.length === 3")
            assert page.locator("#player").evaluate("player => player.paused")
            page.wait_for_function("() => !document.querySelector('#player').paused")
            page.wait_for_function("() => window.scheduledClicks.length > 3")
            metronome_volume.press("Home")
            assert page.evaluate("window.clickGain.gain.value") == 0
            assert page.locator("#player").evaluate("player => player.volume") == 1
            metronome_volume.press("End")
            volume.press("Home")
            assert page.evaluate("window.clickGain.gain.value") == 1
            volume.press("End")
            page.locator("#play").click()
            page.locator("#metronome").uncheck()
            page.locator("#count-in").uncheck()
            page.locator("#metronome-offset").fill("-35")
            page.locator("#metronome-offset").press("Tab")
            with page.expect_download() as file:
                page.get_by_role("button", name="Save session").click()
            saved = Path(root) / "reopen.music-analyzer.zip"
            file.value.save_as(saved)
            page.on("dialog", lambda dialog: dialog.accept())
            page.locator("#open").click()
            page.locator("#files").set_input_files(saved)
            with page.expect_response(lambda response: response.request.method == "DELETE") as replacement:
                page.get_by_role("button", name="Load", exact=True).click()
            assert replacement.value.status == 204
            expect(page.locator("#open")).to_be_enabled()
            expect(page.locator("#placement")).to_contain_text("5 strings")
            expect(page.locator("#score-image #n0-0 text")).to_have_text("(0)")
            expect(page.locator("#loop-from")).to_have_value("2")
            expect(page.locator("#loop-through")).to_have_value("3")
            expect(page.locator("#metronome-offset")).to_have_value("-35")
            expect(page.locator("#score-image .is-looped")).to_have_count(2)
            page.locator("#loop").click()
            assert page.evaluate("dispatchEvent(new Event('beforeunload', { cancelable: true }))")
            for _ in range(4):
                page.locator("#open").click()
                page.locator("#files").set_input_files({
                    "name": "broken.music-analyzer.zip", "mimeType": "application/zip", "buffer": b"invalid",
                })
                page.get_by_role("button", name="Load", exact=True).click()
                expect(page.get_by_role("alert")).to_contain_text("The archive is corrupt")
                expect(page.locator("#placement")).to_contain_text("5 strings")
            selected_notes = page.get_by_role("button", name="Play notes", exact=True)
            selected_notes.click()
            expect(selected_notes).to_have_attribute("aria-pressed", "true")
            page.wait_for_function("() => !document.querySelector('#player').hasAttribute('src')")
            assert page.locator("#player").evaluate("player => player.volume") == 1
            page.locator("#first-note").click()
            page.get_by_role("button", name="Play", exact=True).click()
            page.wait_for_function("() => document.querySelector('#seek').valueAsNumber > .1")
            page.get_by_role("button", name="Pause", exact=True).click()
            page.locator("#practice-all").click()
            expect(page.locator("#practice-all")).to_have_attribute("aria-pressed", "true")
            page.locator("#tracks").get_by_role("button", name="Piano").click()
            page.get_by_role("button", name="Piano roll").click()
            expect(page.locator(".piano-key[data-pitch='67']")).to_have_class("grid-label piano-key playing")
            page.get_by_role("button", name="Sheet music", exact=True).click()
            expect(page.locator("#score-image g.note").first).to_be_visible()
            with page.expect_download() as xml:
                page.get_by_role("button", name="MusicXML", exact=True).click()
            assert xml.value.suggested_filename == "transcription.musicxml"
            page.locator("#tracks").get_by_role("button", name="Drums").click()
            page.get_by_role("button", name="Drum hits").click()
            expect(page.get_by_text("Kick", exact=True)).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.locator("#backing-controls")).to_have_js_property("open", False)
            assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
            assert not errors, errors
            if not page.locator("#numerator").is_visible():
                page.get_by_text("Rhythm", exact=True).click()
            page.locator("#numerator").fill("5")
            page.locator("#rhythm-form button[type=submit]").click()
            expect(page.locator(".ruler-beat").nth(5)).to_have_text("2.1")
            assert not page.evaluate("dispatchEvent(new Event('beforeunload', { cancelable: true }))")
            page.locator("#tracks").get_by_role("button", name="Drop F bass").click()
            reference = mido.MidiFile(ticks_per_beat=480)
            reference.add_track("Reference bass").extend([
                mido.Message("program_change", program=33),
                mido.Message("note_on", note=28, velocity=80),
                mido.Message("note_off", note=28, time=480),
            ])
            midi = Path(root) / "reference.mid"
            reference.save(midi)
            page.locator("#add-midi-file").set_input_files(midi)
            expect(page.locator("#reference-midi")).to_contain_text("Reference bass")
            tracks = page.locator("#tracks")
            tracks.get_by_role("button", name="Reference bass").click()
            expect(tracks.get_by_role("button", name="Reference bass")).to_have_attribute("aria-pressed", "true")
            editor = page.locator("#edit")
            if not editor.evaluate("element => element.open"):
                editor.locator("summary").click()
            page.get_by_role("combobox", name="Note head for note at 0:00.00").select_option("ghost")
            expect(page.locator("#score-image #n0-0 text")).to_have_text("(0)")
            tracks.get_by_role("button", name="Drop F bass").click()
            with page.expect_response(lambda response: response.url.endswith("/midi")
                                      and "/tracks/" in response.url) as adopted:
                page.locator("#apply-midi").click()
            assert adopted.value.status == 200
            expect(page.locator("#tracks").get_by_role("button", name="Drop F bass")).to_contain_text("1 note")
            expect(page.locator("#score-image #n0-0 text")).to_have_text("(4)")
            assert not errors, errors
        finally:
            browser.close()
            server.shutdown()
            thread.join()
            app.extensions["studio_executor"].shutdown(wait=True, cancel_futures=True)
    print("Browser checks passed")


if __name__ == "__main__":
    run()
