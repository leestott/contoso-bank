"use strict";
window.BakuVoice = function ({config, getSession, refresh, notice, showResult}) {
  const element = id => document.getElementById(id);
  let socket, stream, context, processor, source, mute, nextAudio = 0, generation = 0, draft;
  let failed = false, responseBusy = true;
  let queuedAudioBytes = 0;
  let locale;
  const playing = new Set();
  function available() { return Boolean(config.voice_profiles?.[locale]?.version); }
  function status(text) { element("voice-status").textContent = text; }
  function updateSendState() {
    element("voice-text-send").disabled = socket?.readyState !== WebSocket.OPEN || responseBusy || playing.size > 0;
  }
  function stopPlayback() {
    playing.forEach(node => node.stop());
    playing.clear();
    queuedAudioBytes = 0;
    nextAudio = context?.currentTime || 0;
    updateSendState();
  }
  function end(message = "Call ended. Microphone off.") {
    generation++;
    if (socket) { socket.onclose = null; socket.close(); socket = null; }
    stream?.getTracks().forEach(track => track.stop());
    source?.disconnect(); processor?.disconnect(); mute?.disconnect();
    stopPlayback();
    if (context) context.close().catch(error => notice(`Audio cleanup failed: ${error.message}`, true));
    context = stream = processor = source = mute = null;
    element("voice-start").disabled = element("voice-text-connect").disabled = !available();
    element("voice-end").disabled = element("voice-text-send").disabled = true;
    status(message);
  }
  function caption(speaker, text) {
    element("voice-empty").hidden = true;
    const item = document.createElement("li");
    item.textContent = `${speaker}: ${text}`;
    element("voice-captions").append(item);
    while (element("voice-captions").children.length > 100) element("voice-captions").firstChild.remove();
    item.scrollIntoView({block: "nearest"});
    return item;
  }
  function play(data) {
    const bytes = Uint8Array.from(atob(data), character => character.charCodeAt(0));
    if (bytes.length % 2) throw new Error("The voice service sent incomplete PCM audio.");
    if (queuedAudioBytes + bytes.length > 8 * 1024 * 1024) {
      throw new Error("The audio memory limit was reached. End and restart the call.");
    }
    const pcm = new Int16Array(bytes.buffer);
    const buffer = context.createBuffer(1, pcm.length, 24000);
    const values = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) values[i] = pcm[i] / 32768;
    const node = context.createBufferSource();
    node.buffer = buffer;
    node.connect(context.destination);
    // Native speech may arrive faster than playback; keep it contiguous with a small jitter buffer.
    if (!playing.size || nextAudio < context.currentTime) nextAudio = context.currentTime + 0.12;
    playing.add(node);
    queuedAudioBytes += bytes.length;
    updateSendState();
    node.onended = () => {
      if (playing.delete(node)) queuedAudioBytes -= bytes.length;
      updateSendState();
    };
    node.start(nextAudio);
    nextAudio += buffer.duration;
  }
  async function connect(microphone) {
    if (!getSession()) return notice("Start a synthetic banking session first.", true);
    if (!available()) return notice(`Native voice for ${locale} is currently unavailable. No English voice is substituted.`, true);
    end();
    const attempt = generation;
    failed = false; draft = null; responseBusy = true;
    element("voice-start").disabled = element("voice-text-connect").disabled = true;
    element("voice-end").disabled = false;
    status(microphone ? "Requesting microphone permission…" : "Connecting in explicitly text-only mode…");
    try {
      if (!window.isSecureContext || !window.AudioContext) throw new Error("Voice requires HTTPS and a supported audio browser.");
      context = new AudioContext({sampleRate: 24000});
      await context.resume();
      if (context.sampleRate !== 24000) throw new Error("This browser cannot open 24 kHz audio. Use current Microsoft Edge or Chrome.");
      if (microphone) {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser has no microphone API. Use current Edge or Chrome.");
        const acquired = await navigator.mediaDevices.getUserMedia({audio: {
          channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true
        }});
        if (attempt !== generation) { acquired.getTracks().forEach(track => track.stop()); return; }
        stream = acquired;
        await context.audioWorklet.addModule("/pcm-worklet.js");
      }
      if (attempt !== generation) return;
      const url = new URL("/api/bank/voice", location.href);
      url.protocol = "wss:"; url.searchParams.set("session", getSession());
      const connection = new WebSocket(url);
      socket = connection;
      socket.onopen = () => {
        if (attempt === generation) connection.send(JSON.stringify({type: "start", csrf_token: config.csrf_token}));
      };
      socket.onmessage = async event => {
        if (attempt !== generation) return;
        try {
          const value = JSON.parse(event.data);
          if (value.type === "ready") {
            if (stream) {
              source = context.createMediaStreamSource(stream);
              processor = new AudioWorkletNode(context, "banking-pcm");
              mute = context.createGain(); mute.gain.value = 0;
              processor.port.onmessage = message => {
                if (socket?.readyState !== WebSocket.OPEN) return;
                let binary = "";
                for (const byte of new Uint8Array(message.data)) binary += String.fromCharCode(byte);
                socket.send(JSON.stringify({type: "audio", data: btoa(binary)}));
              };
              source.connect(processor).connect(mute).connect(context.destination);
            }
            status(stream ? `Microphone live · ${value.locale} native voice · speak to interrupt.` :
              `Text-only ${value.locale} live agent connection · microphone off.`);
            element("voice-end").focus();
          } else if (value.type === "turn_state") {
            responseBusy = value.busy;
            updateSendState();
          } else if (value.type === "input_refused") {
            notice(value.message, true);
          } else if (value.type === "response.output_audio.delta") {
            play(value.delta);
          } else if (value.type === "input_audio_buffer.speech_started") {
            stopPlayback(); draft = null;
            status("Listening · interrupted audio stopped.");
          } else if (value.type === "conversation.item.input_audio_transcription.completed") {
            caption("Caller", value.transcript);
          } else if (value.type === "response.output_audio_transcript.delta") {
            if (!draft) draft = caption("AI", "");
            draft.textContent += value.delta;
          } else if (value.type === "response.output_audio_transcript.done") {
            if (draft) draft.textContent = `AI: ${value.transcript}`;
            else caption("AI", value.transcript);
            draft = null;
          } else if (value.type === "tool_result") {
            showResult(value.operation, value.result);
            if (value.result.error) notice(value.result.error, true);
            await refresh();
          } else if (value.type === "error") {
            throw new Error(value.error?.message || "The native voice service reported an error.");
          }
        } catch (error) {
          failed = true; notice(error.message, true); end("Voice stopped after an error. Microphone off.");
        }
      };
      socket.onerror = () => {
        if (attempt !== generation) return;
        failed = true; notice("Voice connection failed. Check sign-in and refresh before retrying.", true);
      };
      socket.onclose = event => {
        if (attempt !== generation) return;
        end(failed || event.code !== 1000 ? "Voice connection closed. Refresh before retrying." : "Call ended. Microphone off.");
      };
    } catch (error) {
      if (attempt !== generation) return;
      notice(`Could not start voice: ${error.message}`, true);
      end("Microphone off. Resolve the error, then retry.");
    }
  }
  element("voice-controls").hidden = !config.cloud;
  element("voice-start").addEventListener("click", () => connect(true));
  element("voice-text-connect").addEventListener("click", () => connect(false));
  element("voice-end").addEventListener("click", () => end());
  element("voice-text-form").addEventListener("submit", event => {
    event.preventDefault();
    const input = element("voice-text");
    if (socket?.readyState !== WebSocket.OPEN || responseBusy || playing.size || !input.value.trim()) return;
    responseBusy = true;
    element("voice-text-send").disabled = true;
    socket.send(JSON.stringify({type: "text", text: input.value.trim()}));
    caption("Caller (typed)", input.value.trim()); input.value = "";
  });
  window.addEventListener("pagehide", () => end());
  function selectLocale(value) {
    if (locale === value) return;
    locale = value;
    end(available() ?
      `Native ${locale} voice selected. Start a new call; banking verification is preserved.` :
      `Native ${locale} voice unavailable. Banking controls remain available.`);
  }
  return {end, selectLocale};
};
