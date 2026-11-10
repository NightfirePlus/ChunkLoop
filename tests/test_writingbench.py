import json
import re
import tempfile
import unittest
from pathlib import Path

from chunkloop.adapters import parse_plan
from chunkloop.datasets.writingbench import (WritingBenchBlockReviewer, WritingBenchCase,
                                             WritingBenchFinalReviewer, load_cases)
from chunkloop.models import Block, Issue, Plan, Review
from tools.run_writingbench import write_outputs


class WritingBenchAdapterTests(unittest.TestCase):
    def setUp(self):
        Path(".tmp").mkdir(exist_ok=True)

    def test_generation_preserves_query_without_rubric(self):
        criterion = {key: "secret rubric" for key in
                     ("name", "criteria_description", "1-2", "3-4", "5-6", "7-8", "9-10")}
        row = {"index": 7, "domain1": "Arts", "domain2": "Story", "lang": "zh",
               "query": "写一篇故事，保留原始换行。\n第二行。", "checklist": [criterion] * 5}
        with tempfile.TemporaryDirectory(dir=".tmp") as temp:
            path = Path(temp) / "cases.jsonl"
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            case = load_cases(path)[7]
        context, evidence = case.generation_input()
        self.assertEqual(context, {"task": row["query"], "response_language": "Chinese"})
        self.assertEqual(evidence, {})
        self.assertNotIn("secret rubric", str(context))

    def test_explicit_word_range_is_only_extracted_when_present(self):
        criterion = {key: "rubric" for key in
                     ("name", "criteria_description", "1-2", "3-4", "5-6", "7-8", "9-10")}
        case = WritingBenchCase(1, "A", "B", "en", "Write 1,000–1,500 words.", (criterion,) * 5)
        self.assertEqual(case.explicit_word_range(), (1000, 1500))
        between = WritingBenchCase(838, "A", "B", "en",
                                   "Word count should be between 3,000 and 3,500 words.", ())
        self.assertEqual(between.explicit_word_range(), (3000, 3500))
        self.assertEqual(between.recommended_generation_mode(), "direct")
        self.assertIsNone(WritingBenchCase(2, "A", "B", "en", "Write a story.",
                                           (criterion,) * 5).explicit_word_range())
        section_only = WritingBenchCase(
            405, "Education", "Curriculum Design", "en",
            "Course Description (Chinese, 300-500 words)\n"
            "Course Description (English, 300-500 words)\n"
            "Course description should be 300-500 words; syllabus has no word limit.", ())
        self.assertIsNone(section_only.explicit_word_range())
        mixed = WritingBenchCase(393, "A", "B", "en",
                                 "2000-2500 words per novel. Total length: 8000-10000 words.", ())
        self.assertEqual(mixed.explicit_word_range(), (8000, 10000))
        per_slogan = WritingBenchCase(426, "A", "B", "en",
                                      "Write several slogans. Each slogan should be 15-20 words.", ())
        self.assertIsNone(per_slogan.explicit_word_range())

    def test_planner_has_no_implicit_block_cap(self):
        rows = [{"id": str(index), "instruction": f"part {index}"}
                for index in range(13)]
        self.assertEqual(len(parse_plan({"blocks": rows}, {}).blocks), 13)
        with self.assertRaises(ValueError):
            parse_plan({"blocks": rows}, {}, max_blocks=12)

    def test_explicit_total_length_allocates_each_block(self):
        case = WritingBenchCase(1, "A", "B", "en", "Write 100-200 words.", ())
        plan = case.with_length_budgets(Plan((Block("a", "Start"), Block("b", "Finish"))))
        self.assertIn("about 75 English words", plan.blocks[0].instruction)
        self.assertIn("100-200 English words", plan.blocks[1].instruction)

    def test_exact_length_corrects_false_model_review(self):
        case = WritingBenchCase(1, "A", "B", "en", "Write 4-6 words.", ())

        async def model_review(output, context, evidence):
            return Review((Issue("length_warning", "close to limit"),))

        import asyncio
        reviewer = WritingBenchFinalReviewer(case, model_review)
        self.assertTrue(asyncio.run(reviewer("one two three four", {}, {})).passed)
        review = asyncio.run(reviewer("one two", {}, {}))
        self.assertEqual(review.issues[0].code, "explicit_length_range")

    def test_chinese_character_range(self):
        case = WritingBenchCase(1, "A", "B", "zh", "请写200-300字。", ())
        self.assertEqual(case.explicit_length(), (200, 300, "Han characters"))
        self.assertEqual(case.measured_han_count("一二，三。abc"), 3)
        plan = case.with_length_budgets(Plan((Block("a", "开始"), Block("b", "结束"))))
        self.assertIn("about 125 Han characters", plan.blocks[0].instruction)
        per_item = WritingBenchCase(798, "A", "B", "zh",
                                    "至少3000字。为每个情节点分配约200-250字展开说明。", ())
        self.assertIsNone(per_item.explicit_han_range())
        overall = WritingBenchCase(760, "A", "B", "zh",
                                   "包括开头和故事展开，整体长度在1500-1800字之间。", ())
        self.assertEqual(overall.explicit_han_range(), (1500, 1800))
        tilde = WritingBenchCase(375, "A", "B", "zh", "写一篇4000～5000字解说稿。", ())
        self.assertEqual(tilde.explicit_han_range(), (4000, 5000))

    def test_preserves_valid_planner_budgets(self):
        case = WritingBenchCase(1, "A", "B", "zh", "请写200-300字。", ())
        plan = Plan((Block("a", "开始，字数预算约100字。"),
                     Block("b", "结束，字数预算约150字。")))
        self.assertEqual(case.with_length_budgets(plan), plan)

    def test_replaces_conflicting_planner_ranges_with_weighted_budgets(self):
        case = WritingBenchCase(89, "A", "B", "en", "Write 3000-4000 words.", ())
        plan = Plan((Block("a", "First. Approximate length: 800-900 words."),
                     Block("b", "Second. Approximate length: 700-800 words."),
                     Block("c", "Third. Approximate length: 600-700 words."),
                     Block("d", "Fourth. Approximate length: 500-600 words."),
                     Block("e", "Fifth. Approximate length: 500-600 words."),
                     Block("f", "Sixth. Approximate length: 500-600 words."),
                     Block("g", "Seventh. Approximate length: 500-600 words.")))
        adapted = case.with_length_budgets(plan)
        budgets = [int(re.search(r"about (\d+) English words", block.instruction).group(1))
                   for block in adapted.blocks]
        self.assertEqual(sum(budgets), 3500)
        self.assertGreater(budgets[0], budgets[-1])
        self.assertTrue(all("Approximate length" not in block.instruction
                            for block in adapted.blocks))

    def test_normalizes_role_specific_lengths_without_conflicting_targets(self):
        case = WritingBenchCase(363, "A", "B", "en", "Write 3000-3500 words.", ())
        plan = Plan((Block("intro", "Write an introduction of approximately 200 words."),
                     Block("body", "Write a chapter (approx. 750-800 words) with examples."),
                     Block("end", "Write a conclusion of approximately 200 words.")))
        adapted = case.with_length_budgets(plan)
        budgets = [int(re.search(r"about (\d+) English words", block.instruction).group(1))
                   for block in adapted.blocks]
        self.assertEqual(sum(budgets), 3250)
        self.assertGreater(budgets[1], budgets[0])
        self.assertTrue(all(not re.search(r"200 words|750-800 words", b.instruction)
                            for b in adapted.blocks))

    def test_keeps_local_quote_length_when_allocating_whole_length(self):
        case = WritingBenchCase(1, "A", "B", "en", "Write 3000-4000 words.", ())
        plan = Plan((Block("a", "Include a quotation of 100 words."),
                     Block("b", "Write the analysis.")))
        adapted = case.with_length_budgets(plan)
        self.assertIn("quotation of 100 words", adapted.blocks[0].instruction)

    def test_preserves_valid_english_planner_ranges(self):
        case = WritingBenchCase(65, "A", "B", "en", "Write 1000-1500 words.", ())
        plan = Plan((Block("a", "First. Provide an approximate length budget of 350-400 words"),
                     Block("b", "Second. Provide an approximate length budget of 250-300 words"),
                     Block("c", "Third. Provide an approximate length budget of 300-350 words"),
                     Block("d", "Fourth. Provide an approximate length budget of 200-250 words")))
        self.assertEqual(case.with_length_budgets(plan), plan)

    def test_chinese_budget_near_upper_bound_is_recentered(self):
        case = WritingBenchCase(260, "A", "B", "zh", "请写2000-3000字。", ())
        plan = Plan((Block("a", "开始，字数预算约1000字。"),
                     Block("b", "中段，字数预算约1000字。"),
                     Block("c", "结束，字数预算约800字。")))
        adapted = case.with_length_budgets(plan)
        self.assertNotEqual(adapted, plan)
        self.assertEqual(sum(int(re.search(r"about (\d+) Han characters", b.instruction).group(1))
                             for b in adapted.blocks), 2500)
        self.assertTrue(all("字数预算" not in b.instruction for b in adapted.blocks))

    def test_routes_large_requested_outputs_by_lower_bound(self):
        def case(index, lang, query):
            return WritingBenchCase(index, "A", "B", lang, query, ())

        self.assertEqual(case(89, "en", "Write 3000-4000 words.")
                         .recommended_generation_mode(), "chunkloop")
        self.assertEqual(case(450, "en", "Write 2000-2500 words.")
                         .recommended_generation_mode(), "direct")
        self.assertEqual(case(363, "en", "Write 3000-3500 words.")
                         .recommended_generation_mode(), "direct")
        self.assertEqual(case(542, "en", "Write 3000-4000 words.")
                         .recommended_generation_mode(), "chunkloop")
        self.assertEqual(case(379, "en", "Approximately 4,000 words for analysis.")
                         .recommended_generation_mode(), "chunkloop")
        self.assertEqual(case(260, "zh", "请写2000-3000字。")
                         .recommended_generation_mode(), "direct")
        self.assertEqual(case(798, "zh", "整篇至少3000字，每个情节点约200-250字。")
                         .recommended_generation_mode(), "chunkloop")

    def test_one_sided_whole_length_scope_and_adaptive_fallback(self):
        def case(index, lang, query):
            return WritingBenchCase(index, "A", "B", lang, query, ())

        minimum = case(490, "en", "The article must be at least 5000 words.")
        self.assertEqual(minimum.whole_length_bounds(), (5000, None, "English words"))
        self.assertTrue(minimum.adaptive_fallback_needed("word " * 3810))
        self.assertFalse(minimum.adaptive_fallback_needed("word " * 5100))
        maximum = case(619, "en", "The article should be within 5000 words.")
        self.assertEqual(maximum.whole_length_bounds(), (None, 5000, "English words"))
        self.assertEqual(maximum.recommended_generation_mode(), "direct")
        self.assertFalse(maximum.adaptive_fallback_needed("word " * 4447))
        chinese = case(584, "zh", "总字数要求不少于2万字。前言1000字左右。")
        self.assertEqual(chinese.whole_length_bounds(), (20000, None, "Han characters"))
        self.assertTrue(chinese.adaptive_fallback_needed("中" * 12658))
        self.assertFalse(chinese.adaptive_fallback_needed("中" * 21000))
        local = case(1, "en", "Each chapter must be at least 500 words.")
        self.assertIsNone(local.whole_length_bounds())

    def test_minimum_length_gets_buffered_block_budgets(self):
        case = WritingBenchCase(490, "A", "B", "en",
                                "The article must be at least 5000 words.", ())
        plan = case.with_length_budgets(Plan((Block("a", "First"), Block("b", "Second"))))
        self.assertEqual(sum(int(re.search(r"about (\d+) English words", b.instruction).group(1))
                             for b in plan.blocks), 5500)
        self.assertTrue(all("at least 5000 English words" in b.instruction
                            for b in plan.blocks))

    def test_official_prediction_shape(self):
        with tempfile.TemporaryDirectory(dir=".tmp") as temp:
            folder = Path(temp)
            write_outputs([{"index": 7, "direct": {"response": "text", "status": "completed"}}], folder)
            line = json.loads((folder / "direct_responses.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(line, {"index": 7, "response": "text"})
            write_outputs([{"index": 8, "selected_mode": "direct",
                            "direct": {"response": "chosen", "status": "completed"}}], folder)
            selected = json.loads((folder / "selected_responses.jsonl")
                                  .read_text(encoding="utf-8"))
            self.assertEqual(selected, {"index": 8, "response": "chosen"})


class WritingBenchBlockReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_ignores_minor_approximate_budget_shortfall(self):
        class StrictReviewer:
            async def review(self, *args):
                return Review((Issue("length_target_violation", "660 versus 700"),))

        case = WritingBenchCase(542, "A", "B", "en", "Write 3000-4000 words.", ())
        block = Block("a", "For this block, aim for about 700 English words.")
        reviewer = WritingBenchBlockReviewer(case, StrictReviewer())
        self.assertTrue((await reviewer.review(block, "word " * 660, {}, {})).passed)
        self.assertFalse((await reviewer.review(block, "word " * 500, {}, {})).passed)

    async def test_exact_block_upper_bound_catches_model_miss(self):
        class PassReviewer:
            async def review(self, *args):
                return Review()

        case = WritingBenchCase(450, "A", "B", "en", "Write 2000-2500 words.", ())
        block = Block("a", "For this block, aim for about 100 English words.")
        reviewer = WritingBenchBlockReviewer(case, PassReviewer())
        self.assertTrue((await reviewer.review(block, "word " * 110, {}, {})).passed)
        review = await reviewer.review(block, "word " * 111, {}, {})
        self.assertEqual(review.issues[0].code, "block_length_overflow")
        ranged = Block("b", "Provide an approximate length budget of 80-100 words")
        self.assertEqual((await reviewer.review(ranged, "word " * 111, {}, {}))
                         .issues[0].code, "block_length_overflow")


if __name__ == "__main__":
    unittest.main()
