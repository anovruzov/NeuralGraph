"""Structured form extraction from the live DOM.

The model never sees raw page HTML. This module walks the DOM and the
accessibility attributes and emits a compact JSON description of the form:
one entry per logical question, with its label, type, allowed values and
constraints. That keeps prompts small and answers checkable.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from playwright.sync_api import Frame, Page

from ..config.logging_setup import get_logger

log = get_logger("browser.dom")

# Evaluated inside the page. Returns a list of logical fields plus page metadata.
EXTRACT_JS = r"""
() => {
  const MAX_TEXT = 400;
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim().slice(0, MAX_TEXT);

  const hiddenByAncestor = (el) => {
    // A step of a multi-step form is display:none until it is reached; its
    // controls must not be treated as part of the current step.
    let node = el;
    while (node && node.nodeType === 1) {
      const style = window.getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden") return true;
      if (node.hasAttribute && node.hasAttribute("hidden")) return true;
      node = node.parentElement;
    }
    return false;
  };

  const isVisible = (el) => {
    if (!el) return false;
    if (hiddenByAncestor(el)) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) {
      // A styled file input or custom select can be sized zero but still usable.
      return el.type === "file" || el.tagName === "SELECT";
    }
    return true;
  };

  const cssPath = (el) => {
    if (el.id && document.querySelectorAll(CSS.escape ? `#${CSS.escape(el.id)}` : `#${el.id}`).length === 1) {
      return `#${CSS.escape ? CSS.escape(el.id) : el.id}`;
    }
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift(`#${CSS.escape ? CSS.escape(node.id) : node.id}`); break; }
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(" > ");
  };

  const labelFor = (el) => {
    // 1. aria-label / aria-labelledby
    if (el.getAttribute("aria-label")) return clean(el.getAttribute("aria-label"));
    const labelledby = el.getAttribute("aria-labelledby");
    if (labelledby) {
      const text = labelledby.split(/\s+/).map(id => {
        const n = document.getElementById(id);
        return n ? n.innerText || n.textContent : "";
      }).join(" ");
      if (clean(text)) return clean(text);
    }
    // 2. <label for=id>
    if (el.id) {
      const lbl = document.querySelector(`label[for="${CSS.escape ? CSS.escape(el.id) : el.id}"]`);
      if (lbl && clean(lbl.innerText)) return clean(lbl.innerText);
    }
    // 3. wrapping <label>
    const wrap = el.closest("label");
    if (wrap && clean(wrap.innerText)) return clean(wrap.innerText);
    // 4. nearest labelling text in the enclosing field group. Climb only while
    //    the container holds this control alone: a container with several
    //    controls would hand back some other field's label.
    let node = el.parentElement;
    for (let depth = 0; node && depth < 5; depth++, node = node.parentElement) {
      const controls = node.querySelectorAll("input:not([type=hidden]), select, textarea");
      if (controls.length > 1) break;
      const lbl = node.querySelector(
        "label, legend, .label, [class*='label'], [class*='Label'], [class*='question']");
      if (lbl && clean(lbl.innerText)) return clean(lbl.innerText);
    }
    // 5. previous sibling text
    let prev = el.previousElementSibling;
    while (prev) {
      const t = clean(prev.innerText || prev.textContent);
      if (t) return t;
      prev = prev.previousElementSibling;
    }
    return clean(el.placeholder || el.name || "");
  };

  const helpFor = (el) => {
    const described = el.getAttribute("aria-describedby");
    if (described) {
      const text = described.split(/\s+/).map(id => {
        const n = document.getElementById(id);
        return n ? n.innerText || n.textContent : "";
      }).join(" ");
      if (clean(text)) return clean(text);
    }
    const group = el.closest("div, fieldset, li, section");
    if (group) {
      const hint = group.querySelector(
        "small, .help, .hint, .description, [class*='help'], [class*='hint'], [class*='description']");
      if (hint && clean(hint.innerText)) return clean(hint.innerText);
    }
    return "";
  };

  const REQUIRED_MARK = /[*✱†]|\(\s*required\s*\)|\brequired\b/i;

  const isRequired = (el) => {
    if (el.required || el.getAttribute("aria-required") === "true") return true;
    if (el.getAttribute("aria-required") === "false") return false;

    const lbl = el.id
      ? document.querySelector(`label[for="${CSS.escape ? CSS.escape(el.id) : el.id}"]`)
      : null;
    if (lbl && REQUIRED_MARK.test(lbl.innerText || "")) return true;

    const labelledby = el.getAttribute("aria-labelledby");
    if (labelledby) {
      for (const id of labelledby.split(/\s+/)) {
        const n = document.getElementById(id);
        if (n && REQUIRED_MARK.test(n.innerText || "")) return true;
      }
    }

    // Climb only while the container holds this control alone: a shared
    // container carries other fields' required markers.
    let node = el.parentElement;
    for (let depth = 0; node && depth < 4; depth++, node = node.parentElement) {
      if (node.querySelectorAll("input:not([type=hidden]), select, textarea").length > 1) break;
      const cls = typeof node.className === "string" ? node.className : "";
      if (/(^|[-_ ])required([-_ ]|$)/i.test(cls)) return true;
      const text = clean(node.innerText).slice(0, 250);
      if (REQUIRED_MARK.test(text)) return true;
    }
    return false;
  };

  const fields = [];
  const seenRadioGroups = new Set();

  const controls = Array.from(document.querySelectorAll(
    "input, select, textarea, [role='combobox'], [role='listbox'], [role='radiogroup'], [contenteditable='true']"
  ));

  for (const el of controls) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || (tag === "select" ? "select" : tag)).toLowerCase();
    if (["hidden", "submit", "button", "reset", "image"].includes(type)) continue;
    if (el.getAttribute("aria-hidden") === "true") continue;
    if (!isVisible(el)) continue;

    const name = el.getAttribute("name") || "";
    const entry = {
      selector: cssPath(el),
      tag,
      type,
      name,
      id: el.id || "",
      label: labelFor(el),
      help: helpFor(el),
      placeholder: clean(el.getAttribute("placeholder") || ""),
      aria_label: clean(el.getAttribute("aria-label") || ""),
      required: isRequired(el),
      disabled: !!el.disabled,
      readonly: !!el.readOnly,
      value: clean((el.value !== undefined ? String(el.value) : "")).slice(0, 200),
      maxlength: el.maxLength && el.maxLength > 0 ? el.maxLength : null,
      pattern: el.getAttribute("pattern") || "",
      autocomplete: el.getAttribute("autocomplete") || "",
      options: [],
      multiple: !!el.multiple,
      accept: el.getAttribute("accept") || "",
      role: el.getAttribute("role") || "",
    };

    if (type === "radio" || type === "checkbox") {
      const groupName = name || labelFor(el);
      const key = `${type}:${groupName}`;
      if (type === "radio") {
        if (seenRadioGroups.has(key)) continue;
        seenRadioGroups.add(key);
        const members = Array.from(
          document.querySelectorAll(`input[type="radio"][name="${CSS.escape ? CSS.escape(name) : name}"]`)
        );
        entry.options = members.map(m => ({
          label: labelFor(m) || clean(m.value), value: m.value, selector: cssPath(m),
          checked: !!m.checked,
        }));
        // The group label sits above the options, never on one input. Climb until
        // a labelling element is found whose text is not one of the option labels.
        const optionTexts = new Set(entry.options.map(o => (o.label || "").toLowerCase()));
        let group = el.parentElement, groupLabel = "";
        for (let depth = 0; group && depth < 5 && !groupLabel; depth++, group = group.parentElement) {
          const candidates = group.querySelectorAll(
            "legend, .label, [class*='label'], [class*='Label'], [class*='question'], label, p, div");
          for (const c of candidates) {
            if (c.querySelector("input, select, textarea")) continue;
            const t = clean(c.innerText);
            if (!t || t.length > 300) continue;
            if (optionTexts.has(t.toLowerCase())) continue;
            groupLabel = t;
            break;
          }
        }
        if (groupLabel) entry.label = groupLabel;
        entry.type = "radio";
      } else {
        entry.checked = !!el.checked;
      }
    }

    if (tag === "select") {
      entry.options = Array.from(el.options || []).map(o => ({
        label: clean(o.label || o.text), value: o.value, selected: !!o.selected,
      })).filter(o => o.label || o.value);
    }

    if (entry.role === "combobox" || entry.role === "listbox") {
      const owned = el.getAttribute("aria-controls") || el.getAttribute("aria-owns");
      const list = owned ? document.getElementById(owned) : null;
      if (list) {
        entry.options = Array.from(list.querySelectorAll("[role='option'], li")).slice(0, 60)
          .map(o => ({ label: clean(o.innerText), value: o.getAttribute("data-value") || clean(o.innerText) }));
      }
      entry.type = "combobox";
    }

    fields.push(entry);
  }

  const buttons = Array.from(document.querySelectorAll(
    "button, input[type='submit'], input[type='button'], a[role='button'], [role='button']"
  )).filter(isVisible).map(b => ({
    selector: cssPath(b),
    text: clean(b.innerText || b.value || b.getAttribute("aria-label") || ""),
    type: (b.getAttribute("type") || "").toLowerCase(),
    disabled: !!b.disabled,
  })).filter(b => b.text).slice(0, 40);

  const errors = Array.from(document.querySelectorAll(
    "[role='alert'], .error, .field-error, [class*='error'], [aria-invalid='true']"
  )).filter(isVisible).map(e => clean(e.innerText)).filter(Boolean).slice(0, 20);

  const bodyText = clean(document.body ? document.body.innerText : "").slice(0, 4000);

  // Native HTML5 constraint validation can block a submit click without any
  // visible message, so report it explicitly rather than seeing "nothing happened".
  const invalid = Array.from(document.querySelectorAll("input, select, textarea"))
    .filter(el => el.willValidate && !el.checkValidity())
    .map(el => ({
      selector: cssPath(el),
      label: labelFor(el),
      message: clean(el.validationMessage || ""),
      hidden: !isVisible(el),
    })).slice(0, 20);

  return {
    url: location.href,
    title: document.title,
    fields,
    buttons,
    errors,
    invalid,
    body_text: bodyText,
    has_form: !!document.querySelector("form"),
    iframes: Array.from(document.querySelectorAll("iframe")).map(f => ({
      src: f.getAttribute("src") || "", id: f.id || "", name: f.getAttribute("name") || "",
    })).slice(0, 10),
  };
}
"""


@dataclass
class FormField:
    selector: str
    tag: str = ""
    type: str = "text"
    name: str = ""
    id: str = ""
    label: str = ""
    help: str = ""
    placeholder: str = ""
    aria_label: str = ""
    required: bool = False
    disabled: bool = False
    readonly: bool = False
    value: str = ""
    maxlength: Optional[int] = None
    pattern: str = ""
    autocomplete: str = ""
    options: list[dict[str, Any]] = field(default_factory=list)
    multiple: bool = False
    accept: str = ""
    role: str = ""
    checked: bool = False
    frame_url: str = ""

    @property
    def key(self) -> str:
        """Stable identity for this question, for logging and answer reuse."""
        return self.name or self.id or self.label or self.selector

    def prompt_view(self) -> dict[str, Any]:
        """Minimal representation sent to the model: only decision-relevant parts."""
        view: dict[str, Any] = {
            "label": self.label or self.aria_label or self.placeholder or self.name,
            "type": self.type,
            "required": self.required,
        }
        if self.help:
            view["help"] = self.help[:300]
        if self.placeholder and self.placeholder != view["label"]:
            view["placeholder"] = self.placeholder
        if self.maxlength:
            view["maxlength"] = self.maxlength
        if self.pattern:
            view["pattern"] = self.pattern
        if self.options:
            view["options"] = [
                {"label": o.get("label", ""), "value": o.get("value", "")}
                for o in self.options[:60]
            ]
        if self.multiple:
            view["multiple"] = True
        if self.accept:
            view["accept"] = self.accept
        return view


@dataclass
class FormSnapshot:
    url: str = ""
    title: str = ""
    fields: list[FormField] = field(default_factory=list)
    buttons: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    body_text: str = ""
    has_form: bool = False
    invalid: list[dict[str, Any]] = field(default_factory=list)
    iframes: list[dict[str, Any]] = field(default_factory=list)
    frame_urls: list[str] = field(default_factory=list)

    def visible_required(self) -> list[FormField]:
        return [f for f in self.fields if f.required and not f.disabled]

    def find_submit(self) -> Optional[dict[str, Any]]:
        """Pick the button that submits the application, never 'save' or 'back'."""
        positive = ("submit application", "submit app", "submit", "apply now",
                    "send application", "finish", "complete application")
        negative = ("save", "back", "cancel", "previous", "draft", "sign in",
                    "log in", "search", "close", "upload", "add", "attach")
        best, best_rank = None, -1
        for button in self.buttons:
            text = (button.get("text") or "").strip().lower()
            if not text or button.get("disabled"):
                continue
            if any(n in text for n in negative) and not text.startswith("submit"):
                continue
            for rank, phrase in enumerate(reversed(positive)):
                if phrase in text and rank > best_rank:
                    best, best_rank = button, rank
        return best

    def find_next(self) -> Optional[dict[str, Any]]:
        """Advance button for a multi-step form."""
        for button in self.buttons:
            text = (button.get("text") or "").strip().lower()
            if button.get("disabled"):
                continue
            if text in ("next", "continue", "save and continue", "next step",
                        "continue to next step", "save & continue"):
                return button
        return None

    def to_prompt_json(self, max_fields: int = 60) -> str:
        return json.dumps(
            {"url": self.url, "title": self.title,
             "fields": [f.prompt_view() for f in self.fields[:max_fields]]},
            ensure_ascii=False,
        )


def _coerce_field(data: dict[str, Any], frame_url: str = "") -> FormField:
    allowed = {f for f in FormField.__dataclass_fields__}
    clean = {k: v for k, v in data.items() if k in allowed}
    clean["frame_url"] = frame_url
    clean.setdefault("selector", "")
    return FormField(**clean)


def extract_form(page: Page, include_frames: bool = True) -> FormSnapshot:
    """Extract the form from the page and, if needed, from embedded ATS iframes."""
    try:
        data = page.evaluate(EXTRACT_JS)
    except Exception as exc:
        log.warning("form extraction failed on main frame", extra={"error": str(exc)[:200]})
        data = {}

    snapshot = FormSnapshot(
        url=data.get("url", page.url),
        title=data.get("title", ""),
        fields=[_coerce_field(f) for f in data.get("fields", [])],
        buttons=data.get("buttons", []),
        errors=data.get("errors", []),
        body_text=data.get("body_text", ""),
        has_form=bool(data.get("has_form")),
        invalid=data.get("invalid", []),
        iframes=data.get("iframes", []),
    )

    if include_frames and (not snapshot.fields or snapshot.iframes):
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                sub = frame.evaluate(EXTRACT_JS)
            except Exception:
                continue
            sub_fields = [_coerce_field(f, frame.url) for f in sub.get("fields", [])]
            if sub_fields:
                snapshot.fields.extend(sub_fields)
                snapshot.buttons.extend(
                    {**b, "frame_url": frame.url} for b in sub.get("buttons", [])
                )
                snapshot.errors.extend(sub.get("errors", []))
                snapshot.invalid.extend(sub.get("invalid", []))
                snapshot.frame_urls.append(frame.url)
                if not snapshot.body_text:
                    snapshot.body_text = sub.get("body_text", "")

    log.debug("extracted form", extra={"url": snapshot.url, "fields": len(snapshot.fields),
                                       "buttons": len(snapshot.buttons)})
    return snapshot


def frame_for(page: Page, frame_url: str) -> Frame | Page:
    if not frame_url:
        return page
    for frame in page.frames:
        if frame.url == frame_url:
            return frame
    return page
