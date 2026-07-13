"""Shared JS fragments used by multiple audit modules.

Every module that runs `page.evaluate()` with element-finding logic
used to define its own `cssPath` and accessible-name helpers inline,
which drifted over time (some modules preferred `id`, some preferred
:nth-of-type, some didn't escape values). Centralizing here keeps
selector quality consistent across the entire audit.

Import and prepend these snippets in module probes:

    from audit._js_helpers import CSS_PATH_JS

    _PROBE_JS = CSS_PATH_JS + r'''
    () => {
        // cssPath() is in scope; use it.
        ...
    }
    '''
"""

from __future__ import annotations

# Selector builder preferring stable hooks over :nth-of-type chains.
# Priority (highest first):
#   1. data-testid / data-test / data-cy / data-qa attribute
#   2. Non-hashy id
#   3. name= attribute on form controls
#   4. :nth-of-type chain capped at 6 segments
#
# IMPORTANT: This snippet is intended to be injected INSIDE the body
# of a page.evaluate arrow function (where function declarations are
# legal statements), not prepended before one. Use as:
#     page.evaluate("() => { " + CSS_PATH_JS + "... real code ... }")
CSS_PATH_JS = r"""
function _aa_isHashyId(id) {
    if (!id) return true;
    if (/^(?:css|mui|chakra|sc|emotion|styled)-/i.test(id)) return true;
    if (/^[0-9a-f_-]{10,}$/i.test(id)) return true;
    return false;
}
function _aa_stableSelector(el) {
    if (!el || el.nodeType !== 1) return '';
    for (const attr of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
        const v = el.getAttribute(attr);
        if (v) return '[' + attr + '="' + CSS.escape(v) + '"]';
    }
    if (el.id && !_aa_isHashyId(el.id)) return '#' + CSS.escape(el.id);
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) {
        const n = el.getAttribute('name');
        if (n) return el.tagName.toLowerCase() + '[name="' + CSS.escape(n) + '"]';
    }
    return null;
}
function cssPath(el) {
    if (!el || el.nodeType !== 1) return '';
    const stable = _aa_stableSelector(el);
    if (stable) return stable;
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur.tagName.toLowerCase() !== 'html') {
        const s = _aa_stableSelector(cur);
        if (s && cur !== el) {
            parts.unshift(s);
            return parts.join(' > ');
        }
        let part = cur.tagName.toLowerCase();
        const parent = cur.parentElement;
        if (parent) {
            const sameTag = [...parent.children].filter(c => c.tagName === cur.tagName);
            if (sameTag.length > 1) {
                part += ':nth-of-type(' + (sameTag.indexOf(cur) + 1) + ')';
            }
        }
        parts.unshift(part);
        cur = cur.parentElement;
        if (parts.length > 6) break;
    }
    return parts.join(' > ');
}
"""

# Walker that traverses BOTH regular DOM and all encountered ShadowRoots.
# Use this instead of plain querySelectorAll when you need to find
# elements inside a component's shadow DOM (web components, Lit, Stencil,
# Ionic, material-web, etc.). Returns a flat array of matching elements.
#
# Call as `queryDeep(document, 'button, [role=button]')` — same signature
# as querySelectorAll but crosses shadow boundaries.
SHADOW_DOM_QUERY_JS = r"""
function queryDeep(root, selector) {
    const results = [];
    function visit(node) {
        if (!node) return;
        // Include matches in this root. querySelectorAll must be
        // called with `this` bound to `node` — we call via direct
        // method access, not via a rebound reference.
        if (typeof node.querySelectorAll === 'function') {
            const found = node.querySelectorAll(selector);
            for (const el of found) results.push(el);
        }
        // Recurse into shadow roots of every element in the tree.
        if (typeof node.querySelectorAll === 'function') {
            const all = node.querySelectorAll('*');
            for (const el of all) {
                if (el.shadowRoot) visit(el.shadowRoot);
            }
        }
    }
    visit(root);
    return results;
}
"""
