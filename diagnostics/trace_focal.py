"""Branch-labeled mirror of harness.choose_focal.

Exists so audit tooling can report *which* resolution path fired for each
task, not just the resolved object - choose_focal itself can't return that
without changing its public return type. tests/test_diagnostics_drift.py
verifies output stays identical to the real function; update this file
whenever choose_focal's branch order or conditions change, or that test
will fail.
"""

from __future__ import annotations

import re
from typing import Any

from harness import (
    FOCAL_NEGATIVE_TERMS,
    FOCAL_POSITIVE_TERMS,
    TaskView,
    _effective_phase,
    _ordinal_indices,
    _split_sentences,
    _walk_strings,
    text_of,
    tokens,
)


def choose_focal_traced(view: TaskView) -> tuple[dict[str, Any], str]:
    objects = view.objects
    if not objects:
        return {}, "no_objects"
    by_id = view.object_by_id()
    by_ref = view.object_by_ref()

    marker_refs = view.record_value("focal_marker_refs")
    trace = view.record_value("focal_resolution_trace")
    if isinstance(marker_refs, dict) and isinstance(trace, dict):
        marker_map = marker_refs.get("marker_to_ref")
        phase_to_marker = trace.get("phase_to_marker")
        if isinstance(marker_map, dict) and isinstance(phase_to_marker, dict):
            phase = _effective_phase(view, trace)
            marker = str(phase_to_marker.get(phase) or "")
            ref = str(marker_map.get(marker) or "")
            if ref in by_ref:
                return by_ref[ref], "marker_trace"

    for record in reversed(view.records):
        for candidate in _walk_strings(record.get("value")):
            if candidate in by_id:
                return by_id[candidate], "direct_object_id"

    history = view.history_text
    refs = re.findall(r"WM-\d+", history)
    if refs:
        unique_refs = list(dict.fromkeys(refs))

        pass_match = re.search(r"(WM-\d+)\s*(?:만|only)\s*(?:통과|pass)", history)
        if pass_match and pass_match.group(1) in by_ref:
            return by_ref[pass_match.group(1)], "pass_fixed_regex"
        fixed_match = re.search(r"(WM-\d+)\s*(?:로|으로)?\s*고정", history)
        if fixed_match and fixed_match.group(1) in by_ref:
            return by_ref[fixed_match.group(1)], "pass_fixed_regex"
        stated_match = re.search(r"참조는\s*(WM-\d+)(?:이다|다)", history)
        if stated_match and stated_match.group(1) in by_ref:
            return by_ref[stated_match.group(1)], "stated_regex"
        binding_match = re.search(r"binding[은는]\s*(WM-\d+)[을를]\s*현재\s*턴의\s*참조로\s*지정", history)
        if binding_match and binding_match.group(1) in by_ref:
            return by_ref[binding_match.group(1)], "stated_regex"

        if (
            len(unique_refs) >= 2
            and any(term in history for term in ("가운데", "중간"))
            and any(term in history for term in ("항목", "후보"))
        ):
            middle_ref = unique_refs[len(unique_refs) // 2]
            if middle_ref in by_ref:
                return by_ref[middle_ref], "middle_candidate"

        ordinal_scores: dict[int, int] = {}
        for sentence in _split_sentences(history):
            indices = _ordinal_indices(sentence)
            if not indices:
                continue
            weight = 10 * sum(term in sentence for term in FOCAL_POSITIVE_TERMS)
            weight -= 10 * sum(term in sentence for term in FOCAL_NEGATIVE_TERMS)
            for index in indices:
                if index < len(unique_refs):
                    ordinal_scores[index] = ordinal_scores.get(index, 0) + weight
        if ordinal_scores:
            best_index = max(ordinal_scores, key=lambda i: ordinal_scores[i])
            if ordinal_scores[best_index] > 0:
                best_ordinal_ref = unique_refs[best_index]
                if best_ordinal_ref in by_ref:
                    return by_ref[best_ordinal_ref], "ordinal_scoring"

        best_ref = ""
        best_score = -10_000
        for index, ref in enumerate(refs):
            ref_index = history.find(ref)
            left = max(history.rfind(".", 0, ref_index), history.rfind("。", 0, ref_index), history.rfind("\n", 0, ref_index))
            right_candidates = [pos for pos in [history.find(".", ref_index), history.find("。", ref_index), history.find("\n", ref_index)] if pos != -1]
            right = min(right_candidates) if right_candidates else len(history)
            window = history[left + 1:right]
            score = index
            score += 10 * sum(term in window for term in FOCAL_POSITIVE_TERMS)
            score -= 10 * sum(term in window for term in FOCAL_NEGATIVE_TERMS)
            if score > best_score:
                best_ref = ref
                best_score = score
        if best_ref in by_ref:
            return by_ref[best_ref], "window_scoring"

    prompt_tokens = tokens(view.prompt)
    best = objects[0]
    best_score = -1
    for obj in objects:
        attrs = obj.get("attrs") or {}
        obj_text = text_of({"type": obj.get("type"), "attrs": attrs}).lower()
        score = sum(1 for token in prompt_tokens if token in obj_text)
        if str(attrs.get("ref_code") or "") and str(attrs.get("ref_code")) in view.history_text:
            score += 2
        if score > best_score:
            best = obj
            best_score = score
    return best, "prompt_token_fallback"
