"""Field-filling primitives.

Each helper returns True only if the value is actually present in the DOM
afterwards: a fill that silently does nothing must not be reported as success.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any, Optional, Union

from playwright.sync_api import Error as PlaywrightError, Frame, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from ..config.logging_setup import get_logger
from .dom import FormField, frame_for

log = get_logger("browser.actions")

Ctx = Union[Page, Frame]

YES = ("yes", "y", "true", "1")
NO = ("no", "n", "false", "0")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def best_option(target: Any, options: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Match an answer to one of a field's allowed values.

    Exact match, then normalized match, then yes/no equivalence, then containment,
    then fuzzy — but only above a high similarity bar, so a wrong option is never
    silently chosen.
    """
    if not options:
        return None
    text = str(target or "")
    if not text:
        return None

    for option in options:
        if option.get("label") == text or option.get("value") == text:
            return option

    want = _norm(text)
    if not want:
        return None
    normalized = [(option, _norm(option.get("label") or ""), _norm(str(option.get("value") or "")))
                  for option in options]

    for option, label, value in normalized:
        if want in (label, value):
            return option

    if want in YES or want in NO:
        family = YES if want in YES else NO
        for option, label, value in normalized:
            if label in family or value in family:
                return option

    contains = [option for option, label, _ in normalized
                if label and (want in label or label in want)]
    if len(contains) == 1:
        return contains[0]
    if contains:
        # Prefer the shortest containing label: "Yes" over "Yes, with conditions".
        return min(contains, key=lambda o: len(_norm(o.get("label") or "")))

    labels = [label for _, label, _ in normalized if label]
    close = difflib.get_close_matches(want, labels, n=1, cutoff=0.86)
    if close:
        for option, label, _ in normalized:
            if label == close[0]:
                return option
    return None


def _locator(ctx: Ctx, field: FormField):
    return ctx.locator(field.selector).first


def fill_text(page: Page, field: FormField, value: str, timeout: int = 10000) -> bool:
    ctx = frame_for(page, field.frame_url)
    try:
        locator = _locator(ctx, field)
        locator.scroll_into_view_if_needed(timeout=timeout)
        locator.fill(str(value), timeout=timeout)
        actual = locator.input_value(timeout=timeout)
        if actual.strip() == str(value).strip():
            return True
        # Inputs that reformat (phone masks) or truncate (maxlength) still count
        # when the value they kept is a prefix of what we typed.
        return bool(actual) and (
            _norm(actual) in _norm(str(value)) or _norm(str(value)).startswith(_norm(actual))
        )
    except (PlaywrightError, PlaywrightTimeout) as exc:
        log.warning("fill_text failed", extra={"selector": field.selector,
                                               "error": str(exc)[:200]})
        return False


def select_option(page: Page, field: FormField, value: Any, timeout: int = 10000) -> bool:
    ctx = frame_for(page, field.frame_url)
    option = best_option(value, field.options)
    if option is None:
        log.warning("no matching option", extra={"selector": field.selector,
                                                 "wanted": str(value)[:80]})
        return False
    locator = _locator(ctx, field)
    for attempt in ("value", "label"):
        try:
            locator.scroll_into_view_if_needed(timeout=timeout)
            if attempt == "value" and option.get("value") not in (None, ""):
                locator.select_option(value=str(option["value"]), timeout=timeout)
            else:
                locator.select_option(label=str(option.get("label") or ""), timeout=timeout)
            chosen = locator.input_value(timeout=timeout)
            if chosen and chosen == str(option.get("value", chosen)):
                return True
            return bool(chosen)
        except (PlaywrightError, PlaywrightTimeout):
            continue
    return False


def check_radio(page: Page, field: FormField, value: Any, timeout: int = 10000) -> bool:
    ctx = frame_for(page, field.frame_url)
    option = best_option(value, field.options)
    if option is None or not option.get("selector"):
        log.warning("no matching radio option", extra={"group": field.key,
                                                       "wanted": str(value)[:80]})
        return False
    try:
        locator = ctx.locator(option["selector"]).first
        locator.scroll_into_view_if_needed(timeout=timeout)
        locator.check(timeout=timeout, force=True)
        return bool(locator.is_checked(timeout=timeout))
    except (PlaywrightError, PlaywrightTimeout) as exc:
        log.warning("check_radio failed", extra={"error": str(exc)[:200]})
        return False


def set_checkbox(page: Page, field: FormField, value: Any, timeout: int = 10000) -> bool:
    ctx = frame_for(page, field.frame_url)
    want = _norm(value) in YES or value is True
    try:
        locator = _locator(ctx, field)
        locator.scroll_into_view_if_needed(timeout=timeout)
        if want:
            locator.check(timeout=timeout, force=True)
        else:
            locator.uncheck(timeout=timeout, force=True)
        return locator.is_checked(timeout=timeout) == want
    except (PlaywrightError, PlaywrightTimeout) as exc:
        log.warning("set_checkbox failed", extra={"error": str(exc)[:200]})
        return False


def upload_file(page: Page, field: FormField, path: str, timeout: int = 20000) -> bool:
    file_path = Path(path)
    if not file_path.exists():
        log.error("upload file missing", extra={"path": str(file_path)})
        return False
    ctx = frame_for(page, field.frame_url)
    try:
        locator = _locator(ctx, field)
        locator.set_input_files(str(file_path), timeout=timeout)
        count = ctx.evaluate(
            "(sel) => { const el = document.querySelector(sel);"
            " return el && el.files ? el.files.length : 0; }",
            field.selector,
        )
        return bool(count)
    except (PlaywrightError, PlaywrightTimeout) as exc:
        log.warning("upload_file failed", extra={"selector": field.selector,
                                                 "error": str(exc)[:200]})
        return False


def fill_combobox(page: Page, field: FormField, value: Any, timeout: int = 10000) -> bool:
    """Custom combobox / autocomplete (Ashby, Workday, react-select).

    Type, wait for the listbox, then click the matching option. Never accepts a
    highlighted-by-default option that does not match the intended value.
    """
    ctx = frame_for(page, field.frame_url)
    text = str(value)
    try:
        locator = _locator(ctx, field)
        locator.scroll_into_view_if_needed(timeout=timeout)
        locator.click(timeout=timeout)
        try:
            locator.fill(text, timeout=timeout)
        except (PlaywrightError, PlaywrightTimeout):
            locator.type(text, delay=25, timeout=timeout)
        page.wait_for_timeout(400)

        option_selectors = ["[role='option']", "li[role='option']", ".select__option",
                            "[class*='option']", "ul li"]
        for selector in option_selectors:
            options = ctx.locator(selector)
            try:
                count = options.count()
            except PlaywrightError:
                continue
            for i in range(min(count, 40)):
                item = options.nth(i)
                try:
                    if not item.is_visible():
                        continue
                    label = (item.inner_text(timeout=2000) or "").strip()
                except (PlaywrightError, PlaywrightTimeout):
                    continue
                if best_option(text, [{"label": label, "value": label}]):
                    item.click(timeout=timeout)
                    return True
        # No listbox appeared: a plain text combobox keeps the typed value.
        try:
            return bool(locator.input_value(timeout=2000))
        except (PlaywrightError, PlaywrightTimeout):
            return False
    except (PlaywrightError, PlaywrightTimeout) as exc:
        log.warning("fill_combobox failed", extra={"selector": field.selector,
                                                   "error": str(exc)[:200]})
        return False


def fill_date(page: Page, field: FormField, value: str, timeout: int = 10000) -> bool:
    """Date inputs want YYYY-MM-DD; text date fields usually want MM/DD/YYYY."""
    text = str(value).strip()
    iso = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$", text)
    if field.type == "date":
        if iso:
            text = f"{iso.group(1)}-{iso.group(2)}-{iso.group(3) or '01'}"
        return fill_text(page, field, text, timeout)
    if iso:
        text = f"{iso.group(2)}/{iso.group(3) or '01'}/{iso.group(1)}"
    return fill_text(page, field, text, timeout)


def click_button(page: Page, selector: str, frame_url: str = "",
                 timeout: int = 15000) -> bool:
    ctx = frame_for(page, frame_url)
    try:
        locator = ctx.locator(selector).first
        locator.scroll_into_view_if_needed(timeout=timeout)
        locator.click(timeout=timeout)
        return True
    except (PlaywrightError, PlaywrightTimeout) as exc:
        log.warning("click failed", extra={"selector": selector, "error": str(exc)[:200]})
        return False


def apply_value(page: Page, field: FormField, value: Any, resume_path: str = "",
                timeout: int = 10000) -> bool:
    """Dispatch to the right primitive for this field type."""
    if field.type == "file":
        return upload_file(page, field, str(value or resume_path), timeout)
    if field.type == "select":
        return select_option(page, field, value, timeout)
    if field.type == "radio":
        return check_radio(page, field, value, timeout)
    if field.type == "checkbox":
        return set_checkbox(page, field, value, timeout)
    if field.type == "combobox" or field.role in ("combobox", "listbox"):
        return fill_combobox(page, field, value, timeout)
    if field.type in ("date", "month"):
        return fill_date(page, field, value, timeout)
    return fill_text(page, field, value, timeout)
