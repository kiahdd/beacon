from __future__ import annotations

from dataclasses import replace
import unittest

from beacon.config import DEFAULT_PREFERENCES
from beacon.models import JobOpportunity
from beacon.scorer import score_job


class ScorerTests(unittest.TestCase):
    """Tests for rule-based job fit scoring."""

    def test_scores_excellent_applied_ai_role_as_apply_now(self) -> None:
        """A senior remote Canada role with target AI skills should rank highly."""
        job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            location="Remote Canada",
            work_mode="Remote",
            salary_range="CA$180k-250k",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents", "Evaluation Frameworks"),
            preferred_skills=("Databricks", "MLflow", "Kubernetes"),
            job_link="https://cohere.ai/careers/123456",
        )

        scored = score_job(job)

        self.assertEqual(scored.category, "Apply now")
        self.assertGreaterEqual(scored.score, 80)
        self.assertIn("strong role-title match", scored.explanation)

    def test_scores_promising_but_incomplete_role_as_investigate(self) -> None:
        """A good recruiter lead with missing location/link should need review."""
        job = JobOpportunity(
            company="Generic AI Lab",
            title="Applied AI role",
            location=None,
            work_mode=None,
            salary_range=None,
            seniority=None,
            required_skills=("AI Agents", "Evaluation Frameworks", "RAG", "Databricks"),
            job_link=None,
        )

        scored = score_job(job)

        self.assertEqual(scored.category, "Investigate")
        self.assertGreaterEqual(scored.score, 60)
        self.assertLess(scored.score, 80)
        self.assertIn("location not scored yet", scored.explanation)

    def test_scores_junior_analyst_role_as_skip(self) -> None:
        """Junior analyst work is outside Beacon's target career direction."""
        job = JobOpportunity(
            company="RetailCo",
            title="Junior Data Analyst",
            location="Toronto",
            salary_range="CA$65,000",
            seniority="Junior",
            required_skills=("Excel", "PowerPoint", "Tableau"),
            job_link=None,
        )

        scored = score_job(job)

        self.assertEqual(scored.category, "Skip")
        self.assertLess(scored.score, 60)
        self.assertIn("junior role", scored.explanation)

    def test_scores_solid_data_scientist_contract_as_investigate(self) -> None:
        """Relevant but incomplete contract roles should survive as Investigate."""
        job = JobOpportunity(
            company="Scotiabank",
            title="Data Scientist",
            location="Toronto",
            work_mode="Hybrid",
            salary_range=None,
            seniority=None,
            required_skills=("Python", "SQL", "Machine Learning", "Forecasting"),
            job_link=None,
        )

        scored = score_job(job)

        self.assertEqual(scored.category, "Investigate")
        self.assertGreaterEqual(scored.score, 60)

    def test_location_does_not_bury_strong_role(self) -> None:
        """Missing geography from a LinkedIn alert should stay review-worthy."""
        job = JobOpportunity(
            company="Reddit, Inc.",
            title="Staff Data Scientist",
            location=None,
            work_mode="Remote",
            salary_range=None,
            seniority="Staff",
            required_skills=("ML Systems", "MLOps"),
            job_link="https://www.linkedin.com/jobs/view/4415116373",
        )

        scored = score_job(job)

        self.assertEqual(scored.category, "Investigate")
        self.assertGreaterEqual(scored.score, 70)
        self.assertIn("location not scored yet", scored.explanation)

    def test_scores_machine_learning_scientist_as_target_role(self) -> None:
        """ML Scientist titles should be treated as first-class target roles."""
        job = JobOpportunity(
            company="Ada CX",
            title="Senior Machine Learning Scientist",
            location=None,
            work_mode="Hybrid",
            salary_range=None,
            seniority="Senior",
            required_skills=("Machine Learning", "ML Systems", "MLOps"),
            job_link="https://www.linkedin.com/jobs/view/4405446860",
        )

        scored = score_job(job)

        self.assertEqual(scored.category, "Apply now")
        self.assertGreaterEqual(scored.score, 80)
        self.assertIn("machine learning scientist", scored.explanation)

    def test_tier_a_companies_get_priority_boost(self) -> None:
        """Explicit Tier A companies should move up in the ranking."""
        job = JobOpportunity(
            company="Waabi",
            title="Senior Machine Learning Engineer",
            location=None,
            salary_range=None,
            seniority="Senior",
            required_skills=("Machine Learning", "MLOps"),
            job_link="https://example.com/waabi",
        )

        scored = score_job(job)

        self.assertEqual(scored.category, "Apply now")
        self.assertIn("tier A company preference: Waabi", scored.explanation)

    def test_personal_company_whitelist_adds_priority_boost(self) -> None:
        """Personal whitelist companies should get a stronger custom boost."""
        preferences = replace(
            DEFAULT_PREFERENCES,
            personal_company_whitelist=("reddit",),
        )
        job = JobOpportunity(
            company="Reddit, Inc.",
            title="Staff Data Scientist, Marketing",
            location=None,
            work_mode="Remote",
            salary_range=None,
            seniority="Staff",
            required_skills=("Experimentation", "Incrementality", "Customer Growth"),
            job_link="https://example.com/reddit",
        )

        baseline = score_job(job)
        boosted = score_job(job, preferences)

        self.assertGreater(boosted.score, baseline.score)
        self.assertIn("personal company whitelist: Reddit", boosted.explanation)

    def test_personal_company_blacklist_forces_skip(self) -> None:
        """A blacklisted company should be skipped even if the role is strong."""
        preferences = replace(
            DEFAULT_PREFERENCES,
            personal_company_blacklist=("cohere",),
        )
        job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            location="Remote Canada",
            work_mode="Remote",
            salary_range="CA$180k-250k",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents", "Evaluation Frameworks"),
            preferred_skills=("Databricks", "MLflow", "Kubernetes"),
            job_link="https://example.com/cohere",
        )

        scored = score_job(job, preferences)

        self.assertEqual(scored.score, 0)
        self.assertEqual(scored.category, "Skip")
        self.assertIn("company is on personal blacklist: Cohere", scored.explanation)

    def test_personal_company_blacklist_wins_over_whitelist(self) -> None:
        """Conflicting personal preferences should fail closed as Skip."""
        preferences = replace(
            DEFAULT_PREFERENCES,
            personal_company_whitelist=("doppel",),
            personal_company_blacklist=("doppel",),
        )
        job = JobOpportunity(
            company="Doppel",
            title="Machine Learning Engineer, Detection Systems",
            seniority="Senior",
            required_skills=("Machine Learning", "ML Systems", "Detection Systems"),
            job_link="https://example.com/doppel",
        )

        scored = score_job(job, preferences)

        self.assertEqual(scored.score, 0)
        self.assertEqual(scored.category, "Skip")
        self.assertIn("company is on personal blacklist: Doppel", scored.explanation)
        self.assertNotIn("personal company whitelist", scored.explanation)

    def test_stackadapt_does_not_match_ada_by_substring(self) -> None:
        """Company matching should not find Ada inside StackAdapt."""
        job = JobOpportunity(
            company="StackAdapt",
            title="Applied Machine Learning Scientist",
            location=None,
            salary_range=None,
            seniority=None,
            required_skills=("Machine Learning", "LLM"),
            job_link="https://example.com/stackadapt",
        )

        scored = score_job(job)

        self.assertIn("tier A company preference: StackAdapt", scored.explanation)
        self.assertIn("strategic next step: StackAdapt moves toward AI engineering", scored.explanation)
        self.assertNotIn("Ada moves", scored.explanation)

    def test_scoring_normalizes_company_and_title_before_explaining(self) -> None:
        """Scorer output should carry normalized display fields forward."""
        job = JobOpportunity(
            company=" mongodb ",
            title="senior ai engineer (remote)",
            location=None,
            salary_range=None,
            seniority="Senior",
            required_skills=("LLM", "RAG"),
            job_link="https://example.com/mongodb",
        )

        scored = score_job(job)

        self.assertEqual(scored.job.company, "MongoDB")
        self.assertEqual(scored.job.title, "Senior AI Engineer")
        self.assertIn("tier B company preference: MongoDB", scored.explanation)

    def test_tier_b_companies_get_smaller_priority_boost(self) -> None:
        """Tier B companies should be preferred, but less strongly than Tier A."""
        job = JobOpportunity(
            company="Clio",
            title="Staff Data Scientist",
            location=None,
            salary_range=None,
            seniority="Staff",
            required_skills=("Machine Learning", "Experimentation"),
            job_link="https://example.com/clio",
        )

        scored = score_job(job)

        self.assertEqual(scored.category, "Apply now")
        self.assertIn("tier B company preference: Clio", scored.explanation)

    def test_conditional_tier_a_companies_need_ai_focus(self) -> None:
        """Workday and Thomson Reuters only get Tier A treatment for AI roles."""
        generic_job = JobOpportunity(
            company="Workday",
            title="Senior Data Analyst",
            location=None,
            salary_range=None,
            seniority="Senior",
            required_skills=("SQL", "Tableau"),
            job_link="https://example.com/workday-analytics",
        )
        ai_job = JobOpportunity(
            company="Thomson Reuters",
            title="Senior Applied AI Engineer",
            location=None,
            salary_range=None,
            seniority="Senior",
            required_skills=("LLM", "RAG", "Evaluation"),
            job_link="https://example.com/thomson-ai",
        )

        generic_scored = score_job(generic_job)
        ai_scored = score_job(ai_job)

        self.assertNotIn("tier A company preference", generic_scored.explanation)
        self.assertIn("needs AI/platform focus", generic_scored.explanation)
        self.assertIn("tier A company preference: Thomson Reuters", ai_scored.explanation)

    def test_tier_c_companies_only_get_boost_for_ai_platform_roles(self) -> None:
        """Banks and insurers should move up only when the role is AI/platform-focused."""
        generic_job = JobOpportunity(
            company="Scotiabank",
            title="Data Scientist",
            location=None,
            salary_range=None,
            seniority=None,
            required_skills=("SQL", "Reporting"),
            job_link="https://example.com/scotia-ds",
        )
        ai_job = JobOpportunity(
            company="Scotiabank",
            title="Data Scientist, Global AI and ML",
            location=None,
            salary_range=None,
            seniority=None,
            required_skills=("LLM", "RAG", "Python pipelines"),
            job_link="https://example.com/scotia-ai",
        )

        generic_scored = score_job(generic_job)
        ai_scored = score_job(ai_job)

        self.assertNotIn("traditional enterprise with AI/platform focus", generic_scored.explanation)
        self.assertIn("traditional enterprise with AI/platform focus: Scotiabank", ai_scored.explanation)
        self.assertGreater(ai_scored.score, generic_scored.score)

    def test_ai_native_company_with_detection_systems_is_strategic_next_step(self) -> None:
        """AI-native ML engineering roles should rank as strong career moves."""
        job = JobOpportunity(
            company="Doppel",
            title="Machine Learning Engineer, Detection Systems",
            location=None,
            salary_range=None,
            seniority=None,
            required_skills=("Machine Learning", "ML Systems", "Detection Systems"),
            job_link="https://example.com/doppel-ml",
        )

        scored = score_job(job)

        self.assertEqual(scored.category, "Apply now")
        self.assertIn("strategic next step: Doppel moves toward AI engineering", scored.explanation)

    def test_traditional_aml_contract_scores_lower_when_not_ai_directional(self) -> None:
        """Good pay at a bank should not outrank weak long-term AI movement."""
        job = JobOpportunity(
            company="TD",
            title="Data Scientist, AML Contract",
            location="Toronto",
            work_mode="Hybrid",
            salary_range="CA$190,000",
            seniority=None,
            required_skills=("SQL", "Compliance", "AML", "Reporting"),
            job_link="https://example.com/td-aml",
        )

        scored = score_job(job)

        self.assertLess(scored.score, 60)
        self.assertEqual(scored.category, "Skip")
        self.assertIn("limited strategic movement", scored.explanation)

    def test_contract_roles_get_explicit_penalty(self) -> None:
        """Contract roles should be explainably lower priority than permanent roles."""
        permanent_job = JobOpportunity(
            company="Scotiabank",
            title="Data Scientist, Global AI and ML",
            location="Toronto",
            work_mode="Hybrid",
            salary_range="CA$190,000",
            seniority="Senior",
            required_skills=("LLM", "RAG", "Machine Learning"),
            job_link="https://example.com/scotia-ai",
        )
        contract_job = JobOpportunity(
            company="Scotiabank",
            title="Data Scientist, Global AI and ML Contract",
            location="Toronto",
            work_mode="Hybrid",
            salary_range="CA$190,000",
            seniority="Senior",
            required_skills=("LLM", "RAG", "Machine Learning"),
            job_link="https://example.com/scotia-ai-contract",
        )

        permanent = score_job(permanent_job)
        contract = score_job(contract_job)

        self.assertLess(contract.score, permanent.score)
        self.assertIn("contract role is less preferred", contract.explanation)

    def test_relocation_roles_get_strong_penalty(self) -> None:
        """Roles requiring relocation should lose priority clearly."""
        remote_job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            location="Remote Canada",
            work_mode="Remote",
            salary_range="CA$180k-250k",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents"),
            job_link="https://example.com/cohere-remote",
        )
        relocation_job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            location="San Francisco, relocation required",
            work_mode="On-site",
            salary_range="CA$180k-250k",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents"),
            job_link="https://example.com/cohere-relocation",
        )

        remote = score_job(remote_job)
        relocation = score_job(relocation_job)

        self.assertLess(relocation.score, remote.score)
        self.assertIn("relocation requirement is a preference mismatch", relocation.explanation)

    def test_prioritizes_senior_data_scientist_in_relevant_business_domain(self) -> None:
        """Marketing, loyalty, and personalization DS roles should get a boost."""
        job = JobOpportunity(
            company="Reddit, Inc.",
            title="Staff Data Scientist, Marketing",
            location=None,
            work_mode="Remote",
            salary_range=None,
            seniority="Staff",
            required_skills=("Experimentation", "Incrementality", "Customer Growth"),
            job_link="https://www.linkedin.com/jobs/view/4415116373",
        )

        scored = score_job(job)

        self.assertEqual(scored.category, "Apply now")
        self.assertGreaterEqual(scored.score, 80)
        self.assertIn("relevant DS domain signal", scored.explanation)

    def test_domain_boost_does_not_prioritize_non_target_roles(self) -> None:
        """Domain words alone should not make a vague marketing role look good."""
        job = JobOpportunity(
            company="RetailCo",
            title="Senior Marketing Manager, Loyalty",
            location=None,
            salary_range=None,
            seniority="Senior",
            required_skills=("CRM", "Lifecycle Marketing"),
            job_link="https://example.com/jobs/1",
        )

        scored = score_job(job)

        self.assertLess(scored.score, 80)
        self.assertNotIn("relevant DS domain signal", scored.explanation)

    def test_recent_posted_date_does_not_affect_score(self) -> None:
        """Recent posted-date text should not create a freshness boost."""
        fresh_job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents"),
            posted_date="0 days ago",
        )
        older_job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents"),
            posted_date="10 days ago",
        )

        fresh = score_job(fresh_job)
        older = score_job(older_job)

        self.assertEqual(fresh.score, older.score)
        self.assertNotIn("fresh posting", fresh.explanation)
        self.assertNotIn("older posting", older.explanation)

    def test_jobs_posted_within_two_hours_are_highlighted(self) -> None:
        """Very recent postings should be highlighted without changing score."""
        fresh_job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents"),
            posted_date="37 minutes ago",
            job_link="https://example.com/fresh",
        )
        vague_same_day_job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents"),
            posted_date="0 days ago",
            job_link="https://example.com/same-day",
        )

        fresh = score_job(fresh_job)
        vague_same_day = score_job(vague_same_day_job)

        self.assertEqual(fresh.score, vague_same_day.score)
        self.assertIn("fresh posting: posted within the last 2 hours", fresh.explanation)
        self.assertNotIn("fresh posting", vague_same_day.explanation)

    def test_jobs_posted_more_than_two_hours_ago_are_not_highlighted(self) -> None:
        """A few-hours-old role should not get the urgent fresh-posting marker."""
        job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents"),
            posted_date="3 hours ago",
            job_link="https://example.com/older-today",
        )

        scored = score_job(job)

        self.assertNotIn("fresh posting", scored.explanation)

    def test_postings_older_than_two_weeks_are_expired(self) -> None:
        """Clearly old postings should be removed from the action list."""
        old_job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents"),
            posted_date="14 days ago",
            job_link="https://example.com/old",
        )

        scored = score_job(old_job)

        self.assertTrue(scored.job.is_expired)
        self.assertEqual(scored.score, 0)
        self.assertEqual(scored.category, "Skip")
        self.assertIn("posting is more than 2 weeks old: 14 days", scored.explanation)
        self.assertIn("job appears expired", scored.explanation)

    def test_postings_under_two_weeks_are_not_expired_by_age(self) -> None:
        """Beacon should not expire jobs before the two-week cutoff."""
        recent_job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents"),
            posted_date="13 days ago",
            job_link="https://example.com/recent",
        )

        scored = score_job(recent_job)

        self.assertFalse(scored.job.is_expired)
        self.assertNotEqual(scored.score, 0)
        self.assertNotIn("job appears expired", scored.explanation)

    def test_old_posting_expiry_parses_weeks_and_months(self) -> None:
        """LinkedIn-style week/month ages should also expire old postings."""
        weeks_old_job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents"),
            posted_date="3 weeks ago",
            job_link="https://example.com/weeks-old",
        )
        months_old_job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents"),
            posted_date="2 months ago",
            job_link="https://example.com/months-old",
        )

        weeks_old = score_job(weeks_old_job)
        months_old = score_job(months_old_job)

        self.assertTrue(weeks_old.job.is_expired)
        self.assertTrue(months_old.job.is_expired)
        self.assertIn("posting is more than 2 weeks old: 21 days", weeks_old.explanation)
        self.assertIn("posting is more than 2 weeks old: 60 days", months_old.explanation)

    def test_expired_jobs_are_skipped_even_when_otherwise_strong(self) -> None:
        """Expired roles should not remain actionable recommendations."""
        job = JobOpportunity(
            company="Cohere",
            title="Senior Applied AI Engineer",
            seniority="Senior",
            required_skills=("LLM", "RAG", "AI Agents"),
            job_link="https://example.com/expired",
            is_expired=True,
        )

        scored = score_job(job)

        self.assertEqual(scored.score, 0)
        self.assertEqual(scored.category, "Skip")
        self.assertIn("job appears expired", scored.explanation)


if __name__ == "__main__":
    unittest.main()
