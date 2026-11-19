// One hidden VM of a lab page with several VMs, in its own Web Worker so it
// runs on its own CPU core instead of sharing the page's thread with the
// visible VM.
//
// Messages from the page:
//   {type: "start", vm: {...}, cmdline, initrd: [url, ...]}
//                                         boot the VM (no screen, keyboard or
//                                         sound); the initrd is those files
//                                         joined end to end (the image, then
//                                         the student's archive for a server lab)
//   {type: "net", frame}                  an Ethernet frame for this VM's NIC
// Messages to the page:
//   {type: "net", frame}                  a frame this VM sent
//   {type: "state", state}                "downloading", "booting", "running"
//   {type: "serial", line}                a line of the VM's serial console
//   {type: "error", message}
"use strict";
self.onerror = message => postMessage({ type: "error", message: String(message) });
self.onunhandledrejection = e => postMessage({ type: "error", message: String(e.reason) });
importScripts("v86/libv86.js");

let emulator = null;
let line = "";

async function joined(urls) {
  const parts = await Promise.all(urls.map(async url => {
    const res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) throw new Error(`could not download ${url} (${res.status})`);
    return res.arrayBuffer();
  }));
  return new Blob(parts).arrayBuffer();
}

self.onmessage = async ({ data }) => {
  if (data.type === "start" && !emulator) {
    const vm = data.vm;
    postMessage({ type: "state", state: "downloading" });
    const urls = data.initrd || ["images/" + vm.initrd];
    const initrd = urls.length === 1 ? { url: urls[0] } : { buffer: await joined(urls) };
    emulator = new V86({
      wasm_path: "v86/v86.wasm",
      memory_size: vm.memory_mb * 1024 * 1024,
      vga_memory_size: 2 * 1024 * 1024,
      bios: { url: "v86/seabios.bin" },
      vga_bios: { url: "v86/vgabios.bin" },
      bzimage: { url: "images/" + vm.bzimage },
      initrd,
      cmdline: data.cmdline,
      disable_keyboard: true,
      disable_mouse: true,
      disable_speaker: true,
      autostart: true,
    });
    emulator.add_listener("emulator-ready", () => postMessage({ type: "state", state: "booting" }));
    // Frames the VM's NIC sends: copy (the buffer belongs to the emulator) and
    // hand the copy to the page, which passes it on to the other VMs.
    emulator.add_listener("net0-send", frame => {
      const copy = new Uint8Array(frame);
      postMessage({ type: "net", frame: copy }, [copy.buffer]);
    });
    emulator.add_listener("serial0-output-byte", byte => {
      if (byte === 10) {
        if (line.includes("Initramfs unpacking failed")) {
          postMessage({ type: "error", message: `the image does not fit in ${vm.memory_mb} MB (${line.trim()})` });
        }
        postMessage({ type: "serial", line });
        line = "";
      } else if (byte !== 13) {
        line += String.fromCharCode(byte);
        // The getty's prompt ("attacker login: ") ends without a newline.
        if (line.endsWith("login: ")) postMessage({ type: "state", state: "running" });
      }
    });
  } else if (data.type === "net" && emulator) {
    emulator.bus.send("net0-receive", data.frame);
  }
};
