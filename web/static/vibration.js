/*! vibration 0.1.1 -- haptic feedback for the mobile web (iOS 26.5+ and Android).
 * https://github.com/ilyassesalama/vibration @ bc889f3 (2026-10-12), собрано esbuild в IIFE
 * (window.NasluxVibration). MIT License, Copyright (c) 2026 Ilyasse:
 * MIT License
 * 
 * Copyright (c) 2026 Ilyasse
 * 
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 * 
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 * 
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
"use strict";
var NasluxVibration = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

  // src/index.ts
  var src_exports = {};
  __export(src_exports, {
    cancel: () => cancel,
    engine: () => engine,
    haptic: () => haptic,
    hapticDrag: () => hapticDrag,
    presets: () => presets
  });

  // src/drag.ts
  var WIDTH = 4e3;
  var SIDES = [WIDTH / 4, WIDTH * 3 / 4];
  var HOLD = 250;
  var REST = "-webkit-appearance:none;appearance:none;position:absolute;left:0;top:0;width:100%;height:100%;margin:0;padding:0;border:0;opacity:0;pointer-events:auto";
  var RETARGETED = [
    "pointerover",
    "pointerenter",
    "pointerdown",
    "pointermove",
    "pointerup",
    "pointercancel",
    "pointerout",
    "pointerleave",
    "gotpointercapture",
    "lostpointercapture",
    "touchstart",
    "touchmove",
    "touchend",
    "touchcancel"
  ];
  var surfaces = /* @__PURE__ */ new WeakMap();
  var drag = null;
  var installed = false;
  function attachDragSurface(element) {
    if (!installed) install();
    const host2 = document.createElement("vibration-drag");
    host2.setAttribute("aria-hidden", "true");
    host2.style.cssText = "all:initial!important;position:absolute!important;inset:0!important;display:block!important;z-index:2147483647!important;contain:strict!important;touch-action:none!important;-webkit-tap-highlight-color:transparent!important;-webkit-user-select:none!important;user-select:none!important;-webkit-touch-callout:none!important";
    const toggle2 = document.createElement("input");
    toggle2.type = "checkbox";
    toggle2.setAttribute("switch", "");
    toggle2.tabIndex = -1;
    toggle2.style.cssText = REST;
    host2.attachShadow({ mode: "closed" }).append(toggle2);
    const surface = { host: host2, toggle: toggle2 };
    surfaces.set(host2, surface);
    const staticPosition = getComputedStyle(element).position === "static";
    const position = element.style.position;
    if (staticPosition) element.style.position = "relative";
    element.append(host2);
    return () => {
      clearTimeout(surface.restore);
      if (drag?.surface === surface) drag = null;
      host2.remove();
      if (staticPosition) element.style.position = position;
    };
  }
  function requestDragTick() {
    if (!drag) return null;
    if (performance.now() - drag.startedAt < HOLD) return false;
    if (globalThis.event === drag.move) {
      if (!drag.crossed) cross(drag);
    } else {
      drag.pending = true;
    }
    return true;
  }
  function cancelDragTick() {
    if (drag) drag.pending = false;
  }
  function install() {
    installed = true;
    const options = { capture: true, passive: true };
    addEventListener("touchstart", onTouchStart, options);
    addEventListener("touchmove", onTouchMove, options);
    addEventListener("touchend", onTouchEnd, options);
    addEventListener("touchcancel", onTouchEnd, options);
    for (const type of RETARGETED) addEventListener(type, retarget, options);
  }
  function onTouchStart(event) {
    const surface = surfaces.get(event.target);
    if (!surface || drag || event.targetTouches.length !== 1) return;
    const touch2 = event.changedTouches[0];
    const target = elementUnder(touch2.clientX, touch2.clientY, surface.host);
    drag = {
      surface,
      touch: touch2.identifier,
      startedAt: performance.now(),
      x: touch2.clientX,
      side: 0,
      pending: false,
      move: null,
      crossed: false,
      target,
      path: pathOf(target)
    };
    place(drag);
  }
  function onTouchMove(event) {
    if (!drag || event.target !== drag.surface.host) return;
    const touch2 = find(event.changedTouches, drag.touch);
    if (!touch2) return;
    drag.x = touch2.clientX;
    drag.move = event;
    drag.crossed = false;
    if (drag.pending && performance.now() - drag.startedAt >= HOLD) {
      drag.pending = false;
      cross(drag);
    } else {
      place(drag);
    }
  }
  function onTouchEnd(event) {
    if (!drag || event.target !== drag.surface.host || find(event.touches, drag.touch)) return;
    const { surface, startedAt } = drag;
    drag = null;
    surface.toggle.style.display = "none";
    void surface.toggle.offsetWidth;
    clearTimeout(surface.restore);
    surface.restore = setTimeout(() => {
      surface.toggle.style.cssText = REST;
    }, Math.max(0, startedAt + HOLD + 50 - performance.now()));
  }
  function cross(current) {
    current.side = current.side === 0 ? 1 : 0;
    current.crossed = true;
    place(current);
  }
  function place({ surface: { host: host2, toggle: toggle2 }, x, side }) {
    const box = host2.getBoundingClientRect();
    const scale = box.width / host2.offsetWidth || 1;
    const left = (x - box.left) / scale - SIDES[side];
    toggle2.style.cssText = `-webkit-appearance:none;appearance:none;position:absolute;top:0;left:${left}px;width:${WIDTH}px;height:8px;margin:0;padding:0;border:0;opacity:0;pointer-events:none`;
    void toggle2.offsetWidth;
  }
  function retarget(event) {
    if (!drag || event.target !== drag.surface.host) return;
    const { target, path } = drag;
    Object.defineProperties(event, {
      target: { configurable: true, get: () => target },
      srcElement: { configurable: true, get: () => target },
      composedPath: { configurable: true, value: () => path.slice() }
    });
  }
  function elementUnder(x, y, host2) {
    for (const element of document.elementsFromPoint(x, y)) {
      if (element !== host2 && !host2.contains(element)) return element;
    }
    return host2.parentElement ?? document.body;
  }
  function pathOf(element) {
    const path = [];
    for (let node = element; node; node = node instanceof ShadowRoot ? node.host : node.parentNode) {
      path.push(node);
    }
    path.push(window);
    return path;
  }
  function find(list, identifier) {
    for (const touch2 of list) if (touch2.identifier === identifier) return touch2;
    return void 0;
  }

  // src/patterns.ts
  var presets = {
    success: [
      { duration: 28, intensity: 0.55 },
      { delay: 70, duration: 42, intensity: 1 }
    ],
    warning: [
      { duration: 40, intensity: 0.85 },
      { delay: 110, duration: 40, intensity: 0.6 }
    ],
    error: [
      { duration: 45, intensity: 0.85 },
      { delay: 45, duration: 45, intensity: 0.85 },
      { delay: 45, duration: 60, intensity: 1 }
    ],
    light: [{ duration: 14, intensity: 0.45 }],
    medium: [{ duration: 22, intensity: 0.7 }],
    heavy: [{ duration: 32, intensity: 1 }],
    soft: [{ duration: 40, intensity: 0.4 }],
    rigid: [{ duration: 12, intensity: 1 }],
    selection: [{ duration: 8, intensity: 0.35 }],
    nudge: [
      { duration: 70, intensity: 0.85 },
      { delay: 90, duration: 45, intensity: 0.35 }
    ],
    buzz: [{ duration: 1e3, intensity: 1 }]
  };
  function resolvePattern(input) {
    if (typeof input === "string") {
      if (!Object.hasOwn(presets, input)) {
        throw new TypeError(`vibration: unknown preset "${input}"`);
      }
      return presets[input].map(resolvePulse);
    }
    if (typeof input === "number") {
      return [resolvePulse({ duration: input })];
    }
    if (!Array.isArray(input)) {
      throw new TypeError("vibration: expected a preset name, a number, or an array");
    }
    return input.map((item, index) => {
      if (typeof item !== "number") return resolvePulse(item);
      if (index % 2 === 1) return null;
      return resolvePulse({ duration: item, delay: index > 0 ? input[index - 1] : 0 });
    }).filter((pulse) => pulse !== null);
  }
  function resolvePulse(pulse) {
    if (typeof pulse !== "object" || pulse === null) {
      throw new TypeError("vibration: each pulse must be an object like { duration: 20 }");
    }
    const { duration, intensity = 1, delay = 0 } = pulse;
    for (const [name, value] of [["duration", duration], ["delay", delay]]) {
      if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
        throw new TypeError(`vibration: ${name} must be a finite number >= 0, got ${String(value)}`);
      }
    }
    if (typeof intensity !== "number" || Number.isNaN(intensity)) {
      throw new TypeError(`vibration: intensity must be a number between 0 and 1, got ${String(intensity)}`);
    }
    return { duration, delay, intensity: Math.min(1, Math.max(0, intensity)) };
  }

  // src/activation.ts
  function activate(path, clickHandled) {
    for (const node of path) {
      if (!(node instanceof Element)) continue;
      const href = linkTarget(node);
      if (href !== null) {
        if (clickHandled) followLink(href, node);
        return;
      }
      if (node instanceof HTMLButtonElement) return;
      if (node.localName === "summary") {
        const details = node.parentElement;
        if (details instanceof HTMLDetailsElement && details.querySelector(":scope > summary") === node) {
          details.open = !details.open;
          return;
        }
      }
      if (node instanceof HTMLLabelElement) {
        activateLabel(node);
        return;
      }
    }
  }
  var SVG_NS = "http://www.w3.org/2000/svg";
  var XLINK_NS = "http://www.w3.org/1999/xlink";
  var LINK_ATTRIBUTES = ["target", "download", "rel", "referrerpolicy", "hreflang", "type", "ping"];
  function linkTarget(node) {
    if ((node instanceof HTMLAnchorElement || node instanceof HTMLAreaElement) && node.hasAttribute("href")) return node.href;
    if (node.localName === "a" && node.namespaceURI === SVG_NS) {
      const href = node.getAttribute("href") ?? node.getAttributeNS(XLINK_NS, "href");
      if (href) return new URL(href, node.baseURI).href;
    }
    return null;
  }
  function followLink(href, source) {
    const link = document.createElement("a");
    link.href = href;
    for (const name of LINK_ATTRIBUTES) {
      const value = source.getAttribute(name);
      if (value !== null) link.setAttribute(name, value);
    }
    link.click();
  }
  var TEXT_ENTRY = "input:not([type=checkbox],[type=radio],[type=button],[type=submit],[type=reset],[type=image],[type=file],[type=color],[type=range]),textarea,select";
  function activateLabel(label3) {
    const { control } = label3;
    if (!control) return;
    control.click();
    if (control.matches(TEXT_ENTRY)) control.focus();
  }

  // src/relay.ts
  var SIZE = 24;
  var LIFETIME = 700;
  var CHILDLESS = /* @__PURE__ */ new Set(["img", "video", "audio", "canvas", "iframe", "embed", "object", "meter", "progress", "br", "hr", "wbr"]);
  var RETARGETED2 = [
    "pointerover",
    "pointerenter",
    "pointerdown",
    "pointermove",
    "pointerup",
    "pointercancel",
    "pointerout",
    "pointerleave",
    "touchstart",
    "touchmove",
    "touchend",
    "touchcancel",
    "mouseover",
    "mouseenter",
    "mousemove",
    "mousedown",
    "mouseup",
    "mouseout",
    "mouseleave",
    "click",
    "dblclick",
    "auxclick",
    "contextmenu",
    "DOMActivate"
  ];
  var host;
  var root;
  var label;
  var toggle;
  var touch = null;
  var dispatching = false;
  var requested = false;
  var prevented = false;
  var expiry;
  function installRelay() {
    host = document.createElement("vibration-relay");
    host.setAttribute("aria-hidden", "true");
    root = host.attachShadow({ mode: "closed" });
    label = document.createElement("label");
    label.style.cssText = "position:absolute;inset:0;display:block;-webkit-tap-highlight-color:transparent;-webkit-touch-callout:none;-webkit-user-select:none;user-select:none";
    toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.setAttribute("switch", "");
    toggle.style.display = "none";
    root.append(label, toggle);
    const options = { capture: true, passive: true };
    addEventListener("pointerdown", onPointerDown, options);
    addEventListener("pointerup", onPointerUp, options);
    addEventListener("pointercancel", onPointerCancel, options);
    addEventListener("scroll", onScroll, options);
    addEventListener("click", onClick, options);
    addEventListener("DOMActivate", onActivate, { capture: true });
    addEventListener("input", onInput, options);
    for (const type of RETARGETED2) addEventListener(type, retarget2, options);
  }
  function requestRelayHaptic() {
    if (dispatching) {
      requested = true;
      updateLabel();
      return true;
    }
    if (touch && !touch.cancelled) {
      touch.pending = true;
      return true;
    }
    return false;
  }
  function cancelRelayHaptic() {
    if (touch) touch.pending = false;
    if (dispatching) {
      requested = false;
      updateLabel();
    }
  }
  function onPointerDown(event) {
    if (event.pointerType === "mouse") return;
    if (!event.isPrimary) {
      if (touch) touch.cancelled = true;
      return;
    }
    const path = event.composedPath();
    if (path[0] === host && touch) {
      touch = { ...touch, x: event.clientX, y: event.clientY, cancelled: false, pending: false };
      return;
    }
    removeRelay();
    const node = path[0];
    const target = node instanceof Element ? node : node instanceof Node ? node.parentElement : null;
    touch = target ? { target, path, x: event.clientX, y: event.clientY, cancelled: false, pending: false } : null;
  }
  function onPointerUp(event) {
    if (!touch || touch.cancelled || !event.isPrimary || event.pointerType === "mouse") return;
    const parent = relayParent(touch.target);
    if (!parent) return;
    parent.append(host);
    place2(touch);
    clearTimeout(expiry);
    expiry = setTimeout(reset, LIFETIME);
  }
  function onPointerCancel(event) {
    if (event.isPrimary && event.pointerType !== "mouse") reset();
  }
  function onScroll() {
    if (touch) touch.cancelled = true;
    if (!dispatching) removeRelay();
  }
  function onClick(event) {
    if (event.target !== host || !touch) return;
    if (dispatching) {
      event.stopImmediatePropagation();
      return;
    }
    dispatching = true;
    requested = touch.pending;
    prevented = false;
    updateLabel();
    Object.defineProperties(event, {
      defaultPrevented: { configurable: true, get: () => prevented },
      preventDefault: {
        configurable: true,
        value() {
          if (!event.cancelable) return;
          prevented = true;
          updateLabel();
        }
      },
      returnValue: {
        configurable: true,
        get: () => !prevented,
        set(value) {
          if (value === false) event.preventDefault();
        }
      }
    });
    setTimeout(reset, 0);
  }
  function onActivate(event) {
    if (event.target !== host || !dispatching || !touch) return;
    if (prevented) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    activate(touch.path, requested);
  }
  function onInput(event) {
    if (event.target === host) event.stopImmediatePropagation();
  }
  function retarget2(event) {
    if (!touch) return;
    const { target, path } = touch;
    if (event.target === host) {
      Object.defineProperties(event, {
        target: { configurable: true, get: () => target },
        srcElement: { configurable: true, get: () => target },
        composedPath: { configurable: true, value: () => path.slice() }
      });
    }
    if ("relatedTarget" in event && event.relatedTarget === host) {
      Object.defineProperty(event, "relatedTarget", { configurable: true, get: () => target });
    }
  }
  function updateLabel() {
    if (requested || prevented) {
      label.append(toggle);
      toggle.disabled = !requested;
    } else {
      root.append(toggle);
      toggle.disabled = false;
    }
  }
  function relayParent(target) {
    if (!(target instanceof HTMLElement) || CHILDLESS.has(target.localName) || target.isContentEditable) return null;
    if (target.closest("input, textarea, select, option, vibration-drag, [data-vibration-ignore]")) return null;
    return target === document.documentElement ? document.body : target;
  }
  function place2({ target, x, y }) {
    const style = host.style;
    style.cssText = "all:initial!important;position:fixed!important;display:block!important;z-index:2147483647!important;contain:strict!important;pointer-events:auto!important;left:0!important;top:0!important;width:100px!important;height:100px!important";
    const origin = host.getBoundingClientRect();
    const scaleX = origin.width / 100 || 1;
    const scaleY = origin.height / 100 || 1;
    const bounds = target.getBoundingClientRect();
    const left = Math.max(x - SIZE / 2, Math.min(bounds.left, x - 1));
    const top = Math.max(y - SIZE / 2, Math.min(bounds.top, y - 1));
    const right = Math.min(x + SIZE / 2, Math.max(bounds.right, x + 1));
    const bottom = Math.min(y + SIZE / 2, Math.max(bounds.bottom, y + 1));
    style.setProperty("left", `${(left - origin.left) / scaleX}px`, "important");
    style.setProperty("top", `${(top - origin.top) / scaleY}px`, "important");
    style.setProperty("width", `${(right - left) / scaleX}px`, "important");
    style.setProperty("height", `${(bottom - top) / scaleY}px`, "important");
  }
  function removeRelay() {
    clearTimeout(expiry);
    host.remove();
    dispatching = false;
    requested = false;
    prevented = false;
    updateLabel();
  }
  function reset() {
    removeRelay();
    touch = null;
  }

  // src/switch.ts
  var MIN_INTERVAL = 26;
  var MAX_INTERVAL = 140;
  var TICK_LENGTH = 40;
  var label2;
  var generation = 0;
  function tickTimes(pulses) {
    const times = [];
    let start = 0;
    for (const { delay, duration, intensity } of pulses) {
      start += delay;
      if (intensity > 0 && duration > 0) {
        const interval = MIN_INTERVAL + (1 - intensity) * (MAX_INTERVAL - MIN_INTERVAL);
        times.push(start);
        for (let at = interval; at + TICK_LENGTH <= duration; at += interval) times.push(start + at);
      }
      start += duration;
    }
    return times;
  }
  function tick() {
    if (!label2) {
      label2 = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.setAttribute("switch", "");
      label2.append(input);
    }
    label2.click();
  }
  function playTicks(pulses) {
    const times = tickTimes(pulses);
    if (times.length === 0) return false;
    const current = ++generation;
    const start = performance.now();
    let index = 0;
    const step = () => {
      if (current !== generation) return;
      const elapsed = performance.now() - start;
      while (index + 1 < times.length && times[index + 1] <= elapsed) index++;
      tick();
      index++;
      if (index < times.length) setTimeout(step, times[index] - (performance.now() - start));
    };
    if (times[0] === 0) step();
    else setTimeout(step, times[0]);
    return true;
  }
  function stopTicks() {
    generation++;
  }

  // src/vibrate.ts
  var CYCLE = 20;
  var MIN_ON = 6;
  function toVibrationPattern(pulses) {
    const pattern = [];
    const push = (on, ms) => {
      if (ms <= 0) return;
      const lastIsOn = pattern.length % 2 === 1;
      if (pattern.length > 0 && lastIsOn === on) {
        pattern[pattern.length - 1] += ms;
        return;
      }
      if (pattern.length === 0 && !on) pattern.push(0);
      pattern.push(ms);
    };
    for (const { delay, duration, intensity } of pulses) {
      push(false, delay);
      if (intensity >= 1) {
        push(true, duration);
        continue;
      }
      if (intensity <= 0) {
        push(false, duration);
        continue;
      }
      for (let remaining = duration; remaining > 0; remaining -= CYCLE) {
        const cycle = Math.min(CYCLE, remaining);
        const on = cycle <= MIN_ON ? cycle : Math.round(MIN_ON + (cycle - MIN_ON) * intensity);
        push(true, on);
        push(false, cycle - on);
      }
    }
    if (pattern.length % 2 === 0) pattern.pop();
    if (pattern.length === 1 && pattern[0] === 0) return [];
    return pattern;
  }
  function vibrate(pulses) {
    const pattern = toVibrationPattern(pulses);
    if (pattern.length === 0) return false;
    return navigator.vibrate(pattern);
  }
  function stopVibrating() {
    navigator.vibrate(0);
  }

  // src/index.ts
  function detectEngine() {
    if (typeof window === "undefined" || typeof navigator === "undefined") return "none";
    if (typeof navigator.vibrate === "function") return "vibrate";
    const iOS = navigator.maxTouchPoints > 0 && CSS.supports("-webkit-touch-callout", "none");
    if (!iOS || !("switch" in HTMLInputElement.prototype)) return "none";
    return labelClickIsTrusted() ? "ios-switch" : "ios-relay";
  }
  function labelClickIsTrusted() {
    const label3 = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    label3.append(input);
    let trusted = false;
    input.addEventListener("click", (event) => {
      trusted = event.isTrusted;
      event.preventDefault();
    });
    label3.click();
    return trusted;
  }
  var engine = detectEngine();
  if (engine === "ios-relay") installRelay();
  function haptic(input = "medium") {
    const pulses = resolvePattern(input);
    const audible = pulses.some((pulse) => pulse.duration > 0 && pulse.intensity > 0);
    if (engine === "ios-relay" || engine === "ios-switch") {
      const dragTick = requestDragTick();
      if (dragTick !== null) return audible && dragTick;
    }
    switch (engine) {
      case "vibrate":
        return vibrate(pulses);
      case "ios-switch":
        return playTicks(pulses);
      case "ios-relay":
        return audible && requestRelayHaptic();
      case "none":
        return false;
    }
  }
  function hapticDrag(element) {
    if (engine !== "ios-relay" && engine !== "ios-switch") return () => {
    };
    return attachDragSurface(element);
  }
  function cancel() {
    switch (engine) {
      case "vibrate":
        stopVibrating();
        break;
      case "ios-switch":
        stopTicks();
        cancelDragTick();
        break;
      case "ios-relay":
        cancelRelayHaptic();
        cancelDragTick();
        break;
    }
  }
  return __toCommonJS(src_exports);
})();
