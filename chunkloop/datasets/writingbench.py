"""WritingBench input adapter. Evaluation rubrics stay outside generation context."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chunkloop.models import Block, Issue, Plan, Review


@dataclass(frozen=True)
class WritingBenchCase:
    index: int
    domain1: str
    domain2: str
    lang: str
    query: str
    checklist: tuple[dict[str, str], ...]

    def generation_input(self) -> tuple[dict[str, str], dict[str, Any]]:
        """Pass the original query unchanged; do not leak scoring criteria."""
        language = {"en": "English", "zh": "Chinese"}.get(self.lang, self.lang)
        return {"task": self.query, "response_language": language}, {}

    def explicit_word_range(self) -> tuple[int, int] | None:
        """Recognize an explicit whole-document target, excluding section-only ranges."""
        pattern = re.compile(r"\b(\d[\d,]*)\s*(?:-|–|—|to)\s*(\d[\d,]*)\s+words?\b",
                             re.IGNORECASE)
        between = re.compile(r"\bbetween\s+(\d[\d,]*)\s+and\s+"
                             r"(\d[\d,]*)\s+words?\b", re.IGNORECASE)
        candidates = []
        explicit_whole = []
        for match in sorted([*pattern.finditer(self.query), *between.finditer(self.query)],
                            key=lambda found: found.start()):
            before = self.query[max(0, match.start() - 90):match.start()].lower()
            after = self.query[match.end():match.end() + 30].lower()
            if re.search(r"(?:course\s+description|each\s+\w+(?:\s+\w+)?\s+(?:should|must|is|be)\s+|"
                         r"(?:each|per)\s+(?:section|chapter|paragraph)|"
                         r"(?:introductory|concluding|specific)\s+section)\b", before) or re.match(
                             r"\s+per\s+(?:section|chapter|paragraph|novel)\b", after):
                continue
            lower, upper = (int(value.replace(",", "")) for value in match.groups())
            if 0 < lower <= upper:
                candidates.append((lower, upper))
                if re.search(r"(?:total|overall|entire|whole)\s+(?:word\s+count|length)\s*[:：]?\s*$",
                             before):
                    explicit_whole.append((lower, upper))
        if len(set(explicit_whole)) == 1 and explicit_whole:
            return explicit_whole[0]
        return candidates[0] if len(set(candidates)) == 1 and candidates else None

    def explicit_han_range(self) -> tuple[int, int] | None:
        """Recognize a whole-document range, excluding per-item ranges."""
        pattern = re.compile(r"(\d[\d,]*)\s*(?:-|–|—|～|~|至|到)\s*(\d[\d,]*)\s*字")
        for match in pattern.finditer(self.query):
            before = self.query[max(0, match.start() - 60):match.start()]
            whole_cue = re.search(r"(?:整体|全文|总)(?:长度|字数)[^。；;\n]{0,12}$", before)
            if not whole_cue and re.search(r"(?:每个|每一|每段|每节|每章|每篇|各段|各章|"
                                           r"开头|结尾|中间|摘要部分|单个情节)"
                                           r"[^。；;\n]{0,35}$", before):
                continue
            lower, upper = (int(value.replace(",", "")) for value in match.groups())
            if 0 < lower <= upper:
                return (lower, upper)
        return None

    def explicit_length(self) -> tuple[int, int, str] | None:
        if self.lang == "en":
            target = self.explicit_word_range()
            return (*target, "English words") if target else None
        if self.lang == "zh":
            target = self.explicit_han_range()
            return (*target, "Han characters") if target else None
        return None

    def whole_length_bounds(self) -> tuple[int | None, int | None, str] | None:
        """Whole-output range or one-sided limit; keep local section limits separate."""
        ranged = self.explicit_length()
        if ranged:
            return ranged
        candidates: list[tuple[str, int]] = []
        if self.lang == "en":
            rules = (
                ("min", r"\b(?:at least|no less than|minimum of|not fewer than)\s*"
                 r"(\d[\d,]*)\s+words?\b"),
                ("max", r"\b(?:within|at most|no more than|up to|under|fewer than)\s*"
                 r"(\d[\d,]*)\s+words?\b"),
            )
            for kind, expression in rules:
                for match in re.finditer(expression, self.query, re.IGNORECASE):
                    before = self.query[max(0, match.start() - 70):match.start()].lower()
                    if re.search(r"\b(?:each|per)\s+(?:\w+\s+){0,2}"
                                 r"(?:section|chapter|paragraph|item)\b|"
                                 r"\b(?:introduction|conclusion|abstract|quotation)\b"
                                 r"[^.!?\n]{0,35}$", before):
                        continue
                    candidates.append((kind, int(match.group(1).replace(",", ""))))
            unit = "English words"
        elif self.lang == "zh":
            rules = (
                ("min", r"(?:不少于|至少|不低于)\s*(\d[\d,]*(?:\.\d+)?)\s*(万)?\s*字"),
                ("min", r"(\d[\d,]*(?:\.\d+)?)\s*(万)?\s*字以上"),
                ("max", r"(?:不超过|至多|不多于)\s*(\d[\d,]*(?:\.\d+)?)\s*(万)?\s*字"),
                ("max", r"(\d[\d,]*(?:\.\d+)?)\s*(万)?\s*字以内"),
            )
            for kind, expression in rules:
                for match in re.finditer(expression, self.query):
                    before = self.query[max(0, match.start() - 45):match.start()]
                    whole_cue = re.search(r"(?:全文|总字数|整篇|整份)[^。；;\n]{0,25}$", before)
                    if not whole_cue and re.search(
                            r"(?:每章|每节|每段|每篇|各章|前言|摘要|结语|第[一二三四五六七八九十\d]+[章节])"
                            r"[^。；;\n]{0,30}$", before):
                        continue
                    amount = int(float(match.group(1).replace(",", "")) *
                                 (10000 if match.group(2) else 1))
                    candidates.append((kind, amount))
            unit = "Han characters"
        else:
            return None
        lower = max((value for kind, value in candidates if kind == "min"), default=None)
        upper = min((value for kind, value in candidates if kind == "max"), default=None)
        if lower is None and upper is None:
            return None
        if lower is not None and upper is not None and lower > upper:
            return None
        return lower, upper, unit

    def requested_output_floor(self) -> int:
        """Conservative size signal for routing, including one-point and section targets."""
        length = self.explicit_length()
        values = [length[0]] if length else []
        if self.lang == "en":
            query = re.sub(r"\b\d[\d,]*\s*(?:-|–|—|to)\s*\d[\d,]*\s+words?\b",
                           " ", self.query, flags=re.IGNORECASE)
            query = re.sub(r"\bbetween\s+\d[\d,]*\s+and\s+\d[\d,]*\s+words?\b",
                           " ", query, flags=re.IGNORECASE)
            values.extend(int(value.replace(",", "")) for value in re.findall(
                r"\b(\d[\d,]*)\s+words?\b", query, re.IGNORECASE))
        elif self.lang == "zh":
            query = re.sub(r"\d[\d,]*\s*(?:-|–|—|～|~|至|到)\s*\d[\d,]*\s*字",
                           " ", self.query)
            values.extend(int(value.replace(",", "")) for value in re.findall(
                r"(\d[\d,]*)\s*字", query))
        return max(values, default=0)

    def recommended_generation_mode(self) -> str:
        """Experimental route: reserve chunk generation for large requested outputs."""
        bounds = self.whole_length_bounds()
        if bounds:
            lower, upper, _ = bounds
            if lower is None:
                return "direct"
            target = (lower + upper) // 2 if upper is not None else lower
            return "chunkloop" if target >= (3500 if upper is not None else 3000) else "direct"
        return "chunkloop" if self.requested_output_floor() >= 3500 else "direct"

    def adaptive_fallback_needed(self, response: str | None) -> bool:
        """Try chunking only when a direct draft misses a large length requirement."""
        if not response:
            return True
        count = (self.measured_word_count(response) if self.lang == "en" else
                 self.measured_han_count(response))
        bounds = self.whole_length_bounds()
        if bounds:
            lower, upper, _ = bounds
            if lower is not None and count < lower:
                return lower >= 3000
            if upper is not None and count > upper:
                return upper >= 3500
            return False
        target = self.requested_output_floor()
        return (self.recommended_generation_mode() == "chunkloop" and
                target >= 3500 and count * 10 < target * 8)

    def whole_length_distance(self, response: str) -> int:
        """Exact distance outside a whole-document bound; zero when compliant."""
        bounds = self.whole_length_bounds()
        if bounds is None:
            return 0
        count = (self.measured_word_count(response) if self.lang == "en" else
                 self.measured_han_count(response))
        lower, upper, _ = bounds
        if lower is not None and count < lower:
            return lower - count
        if upper is not None and count > upper:
            return count - upper
        return 0

    def with_length_budgets(self, plan: Plan) -> Plan:
        """Allocate a whole-document range or minimum across blocks."""
        bounds = self.whole_length_bounds()
        if bounds is None or bounds[0] is None:
            return plan
        lower, upper, unit = bounds
        if self.lang == "zh":
            pattern = re.compile(r"字数预算\s*[:：]?\s*约?\s*(\d[\d,]*)\s*字")
            range_pattern = re.compile(r"(?:约|大约)?\s*(\d[\d,]*)\s*(?:-|–|—|至|到)\s*"
                                       r"(\d[\d,]*)\s*字")
        else:
            pattern = re.compile(r"(?:word budget|target|aim for)\s*(?:of|about|approximately)?\s*"
                                 r"(\d[\d,]*)\s*words?", re.IGNORECASE)
            range_pattern = re.compile(r"(?:provide an?\s+)?(?:approximate\s+)?"
                                       r"(?:length\s+budget|length|word\s+budget)\s*(?:of|[:：])?\s*"
                                       r"(\d[\d,]*)\s*(?:-|–|—|to)\s*(\d[\d,]*)\s*words?",
                                       re.IGNORECASE)
        proposed: list[int | None] = []
        for block in plan.blocks:
            ranged = range_pattern.search(block.instruction)
            exact = pattern.search(block.instruction)
            informal = None
            if self.lang == "en":
                for match in re.finditer(
                        r"\b(\d[\d,]*)(?:\s*(?:-|–|—|to)\s*(\d[\d,]*))?\s+words?\b",
                        block.instruction, re.IGNORECASE):
                    before = block.instruction[max(0, match.start() - 40):match.start()]
                    if re.search(r"(?:approx(?:imately)?\.?|about|around|roughly|"
                                 r"(?:word|length)\s+(?:budget|target))\s*(?:of|[:：])?\s*$",
                                 before, re.IGNORECASE):
                        informal = match
                        break
            if ranged:
                proposed.append(sum(int(value.replace(",", "")) for value in ranged.groups()) // 2)
            elif exact:
                proposed.append(int(exact.group(1).replace(",", "")))
            elif informal:
                values = [int(value.replace(",", "")) for value in informal.groups()
                          if value is not None]
                proposed.append(sum(values) // len(values))
            else:
                proposed.append(None)
        target = ((lower + upper) // 2 if upper is not None else
                  (lower * 11 + 9) // 10)
        if (upper is not None and all(value is not None for value in proposed) and
                lower <= sum(proposed) <= target):
            return plan
        weights = [value or 1 for value in proposed] if all(proposed) else [1] * len(plan.blocks)
        total = sum(weights)
        budgets = [target * weight // total for weight in weights]
        for position in range(target - sum(budgets)):
            budgets[position % len(budgets)] += 1
        blocks = []
        for position, block in enumerate(plan.blocks):
            instruction = range_pattern.sub("", pattern.sub("", block.instruction))
            if self.lang == "en":
                numeric_words = r"\d[\d,]*(?:\s*(?:-|–|—|to)\s*\d[\d,]*)?\s+words?\b"
                instruction = re.sub(
                    r"\(\s*(?:(?:approx(?:imately)?\.?|about|around|roughly)\s*)?"
                    + numeric_words + r"\s*\)", "", instruction, flags=re.IGNORECASE)
                instruction = re.sub(
                    r"\b(?:of\s+)?(?:about|approximately|approx\.?|around|roughly)\s+"
                    + numeric_words, "", instruction, flags=re.IGNORECASE)
                instruction = re.sub(r"[ \t]{2,}", " ", instruction)
            whole_target = (f"{lower}-{upper}" if upper is not None else
                            f"at least {lower}")
            blocks.append(Block(
                block.id,
                instruction.rstrip(" .\n") +
                f"\nFor this block, aim for about {budgets[position]} "
                f"{unit}. The assembled response must have {whole_target} {unit}.",
                block.depends_on, block.evidence_ids,
            ))
        return Plan(tuple(blocks))

    def measured_word_count(self, response: str) -> int:
        return len(re.findall(r"\b[\w'-]+\b", response))

    def measured_han_count(self, response: str) -> int:
        return len(re.findall(r"[\u4e00-\u9fff]", response))


class WritingBenchFinalReviewer:
    """Use exact counts to correct model whole-length findings."""

    def __init__(self, case: WritingBenchCase, model_reviewer: Any):
        self.case = case
        self.model_reviewer = model_reviewer

    async def __call__(self, output: Any, context: dict, evidence: dict) -> Review:
        review = await self.model_reviewer(output, context, evidence)
        bounds = self.case.whole_length_bounds()
        if bounds is None or not isinstance(output, str):
            return review
        lower, upper, unit = bounds
        count = (self.case.measured_word_count(output) if self.case.lang == "en"
                 else self.case.measured_han_count(output))
        issues = tuple(issue for issue in review.issues
                       if "length" not in issue.code.lower() and "字数" not in issue.code)
        if (lower is not None and count < lower) or (upper is not None and count > upper):
            target = (f"{lower}-{upper}" if lower is not None and upper is not None else
                      f"at least {lower}" if lower is not None else f"at most {upper}")
            issues += (Issue("explicit_length_range",
                             f"Output has {count} {unit}; requested {target} {unit}.",
                             "full output"),)
        return Review(issues)


class WritingBenchBlockReviewer:
    """Add an exact upper length guard to model block review."""

    def __init__(self, case: WritingBenchCase, model_reviewer: Any):
        self.case = case
        self.model_reviewer = model_reviewer

    async def review(self, block: Block, content: Any, context: dict,
                     evidence: dict) -> Review:
        review = await self.model_reviewer.review(block, content, context, evidence)
        bounds = self.case.whole_length_bounds()
        if not isinstance(content, str) or bounds is None or bounds[0] is None:
            return review
        unit = "English words" if self.case.lang == "en" else "Han characters"
        budget = re.search(r"aim for about\s+(\d[\d,]*)\s+" + re.escape(unit),
                           block.instruction, re.IGNORECASE)
        if budget:
            target = int(budget.group(1).replace(",", ""))
        elif self.case.lang == "zh":
            chinese = re.search(r"字数预算\s*[:：]?\s*约?\s*(\d[\d,]*)\s*字",
                                block.instruction)
            if chinese is None:
                return review
            target = int(chinese.group(1).replace(",", ""))
        else:
            english = re.search(r"(?:provide an?\s+)?(?:approximate\s+)?"
                                r"(?:length\s+budget|length|word\s+budget)\s*(?:of|[:：])?\s*"
                                r"\d[\d,]*\s*(?:-|–|—|to)\s*(\d[\d,]*)\s*words?",
                                block.instruction, re.IGNORECASE)
            if english is None:
                return review
            target = int(english.group(1).replace(",", ""))
        count = (self.case.measured_word_count(content) if self.case.lang == "en"
                 else self.case.measured_han_count(content))
        ceiling = (target * 11 + 9) // 10
        if count * 100 >= target * 85 and count <= ceiling:
            # Model judges sometimes reject a harmless 5% shortfall even though
            # the allocation is approximate and whole-document length is checked.
            review = Review(tuple(issue for issue in review.issues
                                  if issue.code.lower() != "length_target_violation"))
        if count <= ceiling:
            return review
        issue = Issue("block_length_overflow",
                      f"This block has {count} {unit}; target is about {target}, "
                      f"with a maximum of {ceiling}. Shorten this block while "
                      "preserving required content.", block.id)
        return Review(tuple(review.issues) + (issue,))


def load_cases(path: Path) -> dict[int, WritingBenchCase]:
    cases: dict[int, WritingBenchCase] = {}
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                index = row["index"]
                if not isinstance(index, int) or isinstance(index, bool) or index < 1:
                    raise ValueError("index must be a positive integer")
                if index in cases:
                    raise ValueError(f"duplicate index {index}")
                fields = (row["domain1"], row["domain2"], row["lang"], row["query"])
                if any(not isinstance(value, str) or not value.strip() for value in fields):
                    raise ValueError("domain, language and query must be nonempty strings")
                checklist = row["checklist"]
                if not isinstance(checklist, list) or len(checklist) != 5:
                    raise ValueError("expected five checklist criteria")
                required = {"name", "criteria_description", "1-2", "3-4", "5-6", "7-8", "9-10"}
                if any(not isinstance(item, dict) or not required <= item.keys()
                       or any(not isinstance(item[key], str) for key in required)
                       for item in checklist):
                    raise ValueError("invalid checklist criterion")
                cases[index] = WritingBenchCase(index, *fields, tuple(checklist))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    if not cases:
        raise ValueError(f"{path}: no cases")
    return cases
