"""Exercise banking caller guidance and voice playback in Edge with deterministic fixtures."""

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest
import urllib.request

try:
    import websocket
except ImportError:
    websocket = None


ROOT = Path(__file__).resolve().parent
EDGE = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / (
    r"Microsoft\Edge\Application\msedge.exe")


@unittest.skipUnless(EDGE.is_file() and websocket, "Microsoft Edge and websocket-client are required.")
class VoicePlaybackTests(unittest.TestCase):
    def assert_browser(self, content, width=1440, screenshot=None):
        with tempfile.TemporaryDirectory(prefix="bank-voice-test-") as directory:
            page = Path(directory) / "fixture.html"
            page.write_text(content, encoding="utf-8")
            profile = Path(directory) / "profile"
            process = subprocess.Popen(
                [str(EDGE), "--headless", "--disable-gpu", "--no-first-run",
                 "--disable-background-networking", "--no-default-browser-check",
                 "--user-data-dir=" + str(profile), "--remote-debugging-port=0", "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            connection = None
            try:
                deadline = time.monotonic() + 15
                port_file = profile / "DevToolsActivePort"
                while not port_file.exists() and time.monotonic() < deadline:
                    self.assertIsNone(process.poll(), "Edge exited before its debugger became ready.")
                    time.sleep(0.05)
                self.assertTrue(port_file.exists(), "Edge debugger did not become ready.")
                port = port_file.read_text(encoding="utf-8").splitlines()[0]
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as response:
                    target = next(item for item in json.load(response) if item["type"] == "page")
                connection = websocket.create_connection(
                    target["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
                sequence = 0

                def command(method, params=None):
                    nonlocal sequence
                    sequence += 1
                    connection.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
                    while True:
                        result = json.loads(connection.recv())
                        if result.get("id") == sequence:
                            self.assertNotIn("error", result, result)
                            return result.get("result", {})

                command("Emulation.setDeviceMetricsOverride", {
                    "width": width, "height": 1000, "deviceScaleFactor": 1, "mobile": width < 760,
                })
                command("Page.navigate", {"url": page.as_uri()})
                deadline = time.monotonic() + 15
                result = {}
                while time.monotonic() < deadline:
                    result = command("Runtime.evaluate", {
                        "expression": "({text: document.getElementById('result')?.textContent, width: innerWidth})",
                        "returnByValue": True,
                    }).get("result", {}).get("value", {})
                    if result.get("text") == "PASS" or result.get("text", "").startswith("FAIL:"):
                        break
                    time.sleep(0.05)
                self.assertEqual(result.get("text"), "PASS", result)
                self.assertEqual(result.get("width"), width, "Unexpected viewport width.")
                if screenshot:
                    image = command("Page.captureScreenshot", {"captureBeyondViewport": True})["data"]
                    screenshot.parent.mkdir(parents=True, exist_ok=True)
                    screenshot.write_bytes(base64.b64decode(image))
            finally:
                try:
                    if connection:
                        command("Browser.close")
                finally:
                    if connection:
                        connection.close()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=10)

    def test_mandate_passcode_visible_without_manual_controls(self):
        page = (ROOT / "web" / "banking.html").read_text(encoding="utf-8")
        page = re.sub(r'<script src="[^"]+" defer></script>', "", page)
        styles = "\n".join((ROOT / "web" / name).read_text(encoding="utf-8")
                           for name in ("style.css", "banking.css"))
        page = re.sub(r'<link rel="stylesheet" href="[^"]+">', "", page)
        script = (ROOT / "web" / "banking.js").read_text(encoding="utf-8")
        harness = r"""
function check(value, message) { if (!value) throw new Error(message); }
try {
  config = {cloud: true, customers: [
    {id: "james", name: "James Carter", demo_dob: "1985-06-21"},
    {id: "leyla", name: "Leyla Mammadova", demo_dob: "1992-04-17"},
    {id: "dmitri", name: "Dmitri Sokolov", demo_dob: "1974-02-08"}
  ]};
  $("session-workspace").hidden = false;
  $("voice-controls").hidden = false;
  for (const locale of ["en-GB", "ru-RU", "az-AZ"]) {
    for (const customer of ["james", "leyla", "dmitri", "james"]) {
      for (const verified of [false, true]) {
        state = {customer, locale, verified, payments: [], mandate: null};
        guide();
        const code = $("demo-passcode"), shown = code.getClientRects().length > 0;
        check(shown === (customer === "james"), "Only the active James session must show the passcode.");
        check(!$("manual-controls").open, "Voice guidance must not depend on expanded manual controls.");
        check(!code.closest("details"), "The passcode must not be hidden in progressive disclosure.");
        check(code.textContent === "246810", "Displayed passcode must match the synthetic fixture.");
        check($("passcode").value === "" && !$("mandate-confirm").checked && !state.mandate,
          "Showing guidance must never fill or confirm a mandate.");
        check($("mandate-guide").textContent.includes("not the SMS verification code"),
          "The mandate passcode must be distinguished from the OTP.");
        check($("mandate-guide").textContent.includes("300 AZN per payment") &&
          $("mandate-guide").textContent.includes("500 AZN per UTC calendar month") &&
          $("mandate-guide").textContent.includes("30 days") &&
          $("mandate-guide").textContent.includes("Contoso Insurance and Baku Demo Utilities"),
          "Exact mandate limits and merchants must be visible.");
        if (customer === "james" && verified) {
          check($("journey-next").textContent.includes("not the SMS verification code"),
            "The active mandate milestone must explain the next action.");
        }
        check(document.documentElement.scrollWidth <= window.innerWidth,
          "Caller guidance must not create horizontal overflow.");
      }
    }
  }
  check(window.fetchCalls === 1, "Rendering a passcode must not trigger banking requests.");
  $("result").textContent = "PASS";
} catch (error) { $("result").textContent = "FAIL: " + error.stack; }
"""
        content = page.replace("</head>", "<style>" + styles + "</style></head>").replace(
            "</body>",
            '<script>window.fetchCalls = 0; window.fetch = () => { window.fetchCalls++; '
            'return new Promise(() => {}); };</script><script>' + script +
            "</script><script>" + harness + "</script></body>")
        for width in (1440, 390):
            with self.subTest(width=width):
                self.assert_browser(content, width, ROOT / ".azure" / f"mandate-guide-{width}.png")

    def test_native_audio_bursts_drain_without_ending_the_call(self):
        script = (ROOT / "web" / "voice.js").read_text(encoding="utf-8")
        harness = r"""
const nodes = [], errors = [];
class AudioClock {
  constructor() { this.currentTime = 0; this.sampleRate = 24000; window.clock = this; }
  async resume() {}
  async close() {}
  createBuffer(channels, length, rate) {
    return {duration: length / rate, getChannelData: () => new Float32Array(length)};
  }
  createBufferSource() {
    const node = {connect() {}, start(at) { this.at = at; },
      stop() { this.stopped = true; }};
    nodes.push(node);
    return node;
  }
}
class NativeSocket {
  static OPEN = 1;
  constructor() {
    this.readyState = 1; this.sent = []; window.connection = this;
    queueMicrotask(() => this.onopen());
  }
  send(raw) { this.sent.push(JSON.parse(raw)); }
  close() { this.readyState = 3; }
  async event(value) { await this.onmessage({data: JSON.stringify(value)}); }
}
window.AudioContext = AudioClock;
window.WebSocket = NativeSocket;
function check(value, message) { if (!value) throw new Error(message); }
async function run() {
  const voice = window.BakuVoice({
    config: {cloud: true, csrf_token: "fixture", voice_profiles: {"en-GB": {version: "6"}}},
    getSession: () => "synthetic-session", refresh: async () => {},
    notice: (message, error) => { if (error) errors.push(message); }, showResult() {}
  });
  voice.selectLocale("en-GB");
  document.getElementById("voice-text-connect").click();
  await new Promise(resolve => setTimeout(resolve, 0));
  const socket = window.connection;
  await socket.event({type: "ready", locale: "en-GB"});
  const chunk = btoa("\0".repeat(48000));
  for (let i = 0; i < 30; i++) {
    await socket.event({type: "response.output_audio.delta", delta: chunk});
  }
  check(errors.length === 0, "A normal 30-second native speech burst must not end the call: " + errors);
  check(socket.readyState === 1 && nodes.length === 30, "All native audio must remain queued.");
  check(nodes[0].at === 0.12, "Playback must start with bounded jitter headroom.");
  for (let i = 1; i < nodes.length; i++) {
    check(Math.abs(nodes[i].at - nodes[i - 1].at - 1) < 1e-8, "PCM chunks must be contiguous.");
  }
  await socket.event({type: "turn_state", busy: false});
  check(document.getElementById("voice-text-send").disabled, "Typed turns must wait for audible playback.");
  document.getElementById("voice-text").value = "Do not overlap";
  document.getElementById("voice-text-form").dispatchEvent(new Event("submit", {cancelable: true}));
  check(socket.sent.length === 1, "Playback must prevent overlapping typed turns.");
  for (const node of [...nodes]) {
    window.clock.currentTime = node.at + node.buffer.duration;
    node.onended();
  }
  check(!document.getElementById("voice-text-send").disabled, "Typed turns must unlock after audio drains.");
  await socket.event({type: "response.output_audio.delta", delta: chunk});
  const resumed = nodes.at(-1);
  check(Math.abs(resumed.at - window.clock.currentTime - 0.12) < 1e-8, "A drained queue needs fresh jitter headroom.");
  await socket.event({type: "input_audio_buffer.speech_started"});
  check(resumed.stopped, "Caller interruption must stop queued speech.");
  resumed.onended();
  for (let i = 0; i < 30; i++) {
    await socket.event({type: "response.output_audio.delta", delta: chunk});
  }
  check(errors.length === 0 && socket.readyState === 1, "Interrupted queues must release their memory accounting.");
  for (let i = 0; i < 150 && socket.readyState === 1; i++) {
    await socket.event({type: "response.output_audio.delta", delta: chunk});
  }
  check(errors.length === 1 && errors[0].includes("memory limit"), "Unbounded audio must still fail explicitly.");
  check(socket.readyState === 3, "The memory safety limit must release the connection.");
  document.getElementById("result").textContent = "PASS";
}
run().catch(error => document.getElementById("result").textContent = "FAIL: " + error.stack);
"""
        controls = "".join(
            f'<{tag} id="{name}"></{tag}>'
            for name, tag in (
                ("voice-status", "p"), ("voice-start", "button"), ("voice-text-connect", "button"),
                ("voice-end", "button"), ("voice-text-send", "button"), ("voice-empty", "p"),
                ("voice-captions", "ol"), ("voice-controls", "div"), ("voice-text-form", "form"),
                ("voice-text", "input"), ("result", "pre"),
            )
        )
        self.assert_browser(controls + "<script>" + script + "</script><script>" + harness + "</script>")


if __name__ == "__main__":
    unittest.main()
