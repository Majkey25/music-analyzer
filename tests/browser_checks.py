"""Run with Playwright Chromium installed; all fixtures are synthetic."""

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

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
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.get_by_role("button", name="Studiová ukázka").click()
            page.locator("#studio").wait_for(state="visible")
            expect(page.locator("#score-image g.note").first).to_be_visible()
            page.get_by_role("button", name="Piano roll").click()
            expect(page.locator(".ruler-beat").first).to_have_text("1.1")
            page.get_by_text("Rytmus", exact=True).click()
            page.locator("#numerator").fill("3")
            page.locator("#rhythm-form button[type=submit]").click()
            expect(page.locator(".ruler-beat").nth(3)).to_have_text("2.1")
            page.get_by_role("button", name="Přehrát", exact=True).click()
            page.wait_for_function("() => document.querySelector('#seek').valueAsNumber > .1")
            page.get_by_role("button", name="Pozastavit").click()
            page.get_by_role("button", name="Tabulatura").click()
            page.locator("#tuning").fill("F0 C1 F1 Bb1 D2")
            page.get_by_role("button", name="Použít ladění").click()
            expect(page.locator("#placement")).to_contain_text("5 strun")
            page.locator("#edit summary").click()
            first = page.locator("#note-table select").first
            first.select_option("0")
            expect(first).to_have_value("0")
            with page.expect_download() as tab:
                page.get_by_role("button", name="TAB .txt").click()
            assert tab.value.suggested_filename == "prepis.txt"
            with page.expect_download() as file:
                page.get_by_role("button", name="Uložit relaci").click()
            saved = Path(root) / "reopen.music-analyzer.zip"
            file.value.save_as(saved)
            page.on("dialog", lambda dialog: dialog.accept())
            page.locator("#open").click()
            page.locator("#files").set_input_files(saved)
            with page.expect_response(lambda response: response.request.method == "DELETE") as replacement:
                page.get_by_role("button", name="Načíst", exact=True).click()
            assert replacement.value.status == 204
            expect(page.locator("#open")).to_be_enabled()
            expect(page.locator("#placement")).to_contain_text("5 strun")
            expect(page.locator("#score-image g.note").first).to_be_visible()
            assert page.evaluate("dispatchEvent(new Event('beforeunload', { cancelable: true }))")
            for _ in range(4):
                page.locator("#open").click()
                page.locator("#files").set_input_files({
                    "name": "broken.music-analyzer.zip", "mimeType": "application/zip", "buffer": b"invalid",
                })
                page.get_by_role("button", name="Načíst", exact=True).click()
                expect(page.get_by_role("alert")).to_contain_text("Archiv je poškozený")
                expect(page.locator("#placement")).to_contain_text("5 strun")
            page.locator("#practice-all").click()
            page.locator("#tracks").get_by_role("button", name="Klavír").click()
            page.get_by_role("button", name="Piano roll").click()
            expect(page.locator(".piano-key[data-pitch='67']")).to_have_class("grid-label piano-key playing")
            page.get_by_role("button", name="Noty", exact=True).click()
            expect(page.locator("#score-image g.note").first).to_be_visible()
            with page.expect_download() as xml:
                page.get_by_role("button", name="MusicXML", exact=True).click()
            assert xml.value.suggested_filename == "prepis.musicxml"
            page.locator("#tracks").get_by_role("button", name="Bicí").click()
            page.get_by_role("button", name="Údery").click()
            expect(page.get_by_text("Kopák", exact=True)).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
            assert not errors, errors
            if not page.locator("#numerator").is_visible():
                page.get_by_text("Rytmus", exact=True).click()
            page.locator("#numerator").fill("5")
            page.locator("#rhythm-form button[type=submit]").click()
            expect(page.locator(".ruler-beat").nth(5)).to_have_text("2.1")
            assert not page.evaluate("dispatchEvent(new Event('beforeunload', { cancelable: true }))")
        finally:
            browser.close()
            server.shutdown()
            thread.join()
            app.extensions["studio_executor"].shutdown(wait=True, cancel_futures=True)
    print("Browser checks passed")


if __name__ == "__main__":
    run()
