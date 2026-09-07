from __future__ import annotations

from collections.abc import Sequence
import json
import re

_BINDING_NAME_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]{0,63}$")


def _normalize_allowed_hosts(allowed_hosts: Sequence[str]) -> tuple[str, ...]:
    if isinstance(allowed_hosts, (str, bytes)):
        raise TypeError("allowed_hosts must be a sequence of host roots")
    normalized = tuple(
        dict.fromkeys(
            host.casefold().strip().rstrip(".")
            for host in allowed_hosts
            if isinstance(host, str) and host.strip()
        )
    )
    if not normalized:
        raise ValueError("allowed_hosts must contain at least one host root")
    return normalized


def build_action_observer_script(
    binding_name: str,
    allowed_hosts: Sequence[str],
) -> str:
    """Build the fixed-shape, metadata-only DOM observer injected by CDP."""

    if not _BINDING_NAME_RE.fullmatch(binding_name):
        raise ValueError("binding_name must be a safe JavaScript identifier")
    binding_literal = json.dumps(binding_name)
    hosts_literal = json.dumps(
        list(_normalize_allowed_hosts(allowed_hosts)),
        separators=(",", ":"),
    )
    return f"""(() => {{
  const BINDING = {binding_literal};
  const ALLOWED_HOSTS = {hosts_literal};
  const currentHost = location.hostname.toLowerCase().replace(/\\.$/, "");
  if (!ALLOWED_HOSTS.some((root) =>
      currentHost === root || currentHost.endsWith(`.${{root}}`))) return;

  const INSTALL_KEY = Symbol.for("bizman.action-observer.v1");
  if (globalThis[INSTALL_KEY]) return;
  globalThis[INSTALL_KEY] = true;

  const bounded = (item, limit) =>
    typeof item === "string" ? item.slice(0, limit) : "";
  const safeId = (item) =>
    /^[A-Za-z_][A-Za-z0-9_.:-]{{0,63}}$/.test(item) ? item : "";

  const elementMeta = (element) => {{
    if (!(element instanceof Element)) return null;
    const tag = bounded(element.tagName, 32).toLowerCase();
    const id = safeId(bounded(element.id, 64));
    return {{
      tag,
      type: bounded(element.getAttribute("type"), 32).toLowerCase(),
      name: bounded(element.getAttribute("name"), 128),
      role: bounded(element.getAttribute("role"), 64),
      selector: id ? `${{tag}}#${{id}}` : tag,
    }};
  }};

  const formMeta = (form) => {{
    if (!(form instanceof HTMLFormElement)) return null;
    let actionPath = "";
    try {{
      const action = new URL(form.action || location.href, location.href);
      if (action.origin === location.origin) actionPath = action.pathname;
    }} catch (_) {{}}
    const fieldNames = [];
    for (const field of Array.from(form.elements).slice(0, 128)) {{
      const name = bounded(field.getAttribute && field.getAttribute("name"), 128);
      if (name) fieldNames.push(name);
    }}
    return {{
      actionPath,
      method: bounded(form.method, 16).toLowerCase(),
      fieldNames,
    }};
  }};

  const emit = (kind, event, element, form) => {{
    const binding = globalThis[BINDING];
    if (typeof binding !== "function") return;
    const payload = {{
      schema: 1,
      kind,
      wallTimeMs: Date.now(),
      performanceTimeMs: performance.now(),
      isTrusted: Boolean(event && event.isTrusted),
      pagePath: location.pathname,
      element: elementMeta(element),
      form: formMeta(form),
    }};
    try {{ binding(JSON.stringify(payload)); }} catch (_) {{}}
  }};

  document.addEventListener("click", (event) => {{
    const element = event.target instanceof Element ? event.target : null;
    const form = element ? element.closest("form") : null;
    emit("click", event, element, form);
  }}, true);

  document.addEventListener("change", (event) => {{
    const element = event.target instanceof Element ? event.target : null;
    const form = element ? element.closest("form") : null;
    emit("change", event, element, form);
  }}, true);

  document.addEventListener("submit", (event) => {{
    const form = event.target instanceof HTMLFormElement ? event.target : null;
    const element = event.submitter instanceof Element ? event.submitter : form;
    emit("submit", event, element, form);
  }}, true);
}})();"""
