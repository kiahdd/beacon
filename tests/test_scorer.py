from __future__ import annotations

import unittest

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

    def test_posted_date_does_not_affect_score(self) -> None:
        """Unreliable posted-date text should stay metadata, not score input."""
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


if __name__ == "__main__":
    unittest.main()
