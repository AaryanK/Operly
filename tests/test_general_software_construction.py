import unittest

from packages.custom_software.construction import build_preview_evidence
from packages.custom_software.generated_engines import apply_match_events,calculate_standings
from packages.custom_software.planner import build_software_plan,revise_plan


FIXTURES={
 "Build a football club and match intelligence platform with leagues, seasons, clubs, squads, players, tactical formations, live match events, automatic standings, player statistics, and analytics.":("football_competition_match_intelligence",{"league","season","club","player","fixture","match_event"},{"standings_engine","live_match_engine"}),
 "Build a stablecoin fruit marketplace selling mangoes apples and fruit boxes with inventory, cart, checkout, USDT and USDC sandbox payments.":("stablecoin_fruit_marketplace",{"product","cart","order","stablecoin_invoice"},{"stablecoin_adapter","payment_lifecycle"}),
 "Build an immersive 3D audio universe with uploads, microphone input, waveform, spectrum, spectrogram, multitrack playback and WebGL particles.":("immersive_3d_audio_universe",{"audio_project","audio_asset","track"},{"audio_analysis","webgl_audio_scene"}),
 "Build a scientific 3D event explorer for CSV and JSON datasets with tracks, hits, vertices, energy mapping, histograms and event filtering.":("scientific_3d_event_explorer",{"dataset","scientific_event","track","hit","vertex"},{"dataset_ingestion","detector_renderer"}),
 "Build an emergency response and field command center with incidents, dispatch, responders, vehicles, maps, offline updates and response analytics.":("emergency_response_field_command",{"incident","dispatch_assignment","responder_team","vehicle"},{"incident_lifecycle","offline_sync"}),
}


class GeneralConstructionFixtureTests(unittest.TestCase):
    def test_five_unrelated_requests_synthesize_custom_architectures(self):
        for prompt,(architecture,entities,engines) in FIXTURES.items():
            with self.subTest(architecture=architecture):
                plan=build_software_plan(prompt);payload=plan.model_dump_json().lower()
                self.assertEqual(plan.primaryArchitecture,"llm_directed_recursive")
                self.assertEqual(plan.implementationMode,"sandbox_generated")
                self.assertTrue(plan.requirementLedger)
                self.assertTrue(plan.planTree)
                self.assertTrue(plan.planningMetrics.globalValidationPassed)
                self.assertTrue(plan.requirementEvidence)
                self.assertNotIn("service_request",payload)
                self.assertNotIn("en_route",payload)
                build=build_preview_evidence(plan)
                self.assertEqual(build["status"],"construction_artifacts_ready")
                self.assertFalse(build["processRunning"])
                self.assertFalse(build["productionDeployment"])
                self.assertEqual(build["testReport"]["failed"],0)
                self.assertTrue(build["sourceManifest"])

    def test_revision_recomputes_affected_architecture(self):
        plan=build_software_plan(next(iter(FIXTURES)))
        revised=revise_plan(plan,"Replace the tactical formation editor with an immersive three-dimensional audio particle environment.")
        self.assertEqual(revised.primaryArchitecture,"llm_directed_recursive")
        self.assertTrue(revised.semanticDiff.structuralChange)
        self.assertIn("immersive three-dimensional audio",revised.model_dump_json().lower())
        self.assertEqual(revised.provenance["revisions"], ["Replace the tactical formation editor with an immersive three-dimensional audio particle environment."])

    def test_football_domain_engines_enforce_invariants(self):
        match=apply_match_events("A","B",[{"minute":10,"type":"goal","club":"A","player":"p1"}])
        self.assertEqual(match["homeScore"],1)
        self.assertEqual(calculate_standings(["A","B"],[match])[0]["points"],3)
        with self.assertRaises(ValueError):apply_match_events("A","A",[])
        with self.assertRaises(ValueError):apply_match_events("A","B",[{"minute":70,"type":"goal","club":"A"},{"minute":20,"type":"goal","club":"B"}])

    def test_preview_has_requirement_to_artifact_and_test_traceability(self):
        plan=build_software_plan(next(iter(FIXTURES)))
        build=build_preview_evidence(plan)
        self.assertTrue(all(x["status"]=="verified" for x in build["requirementEvidence"]))
        self.assertFalse(build["processRunning"])
        self.assertIn("architecture preview",build["previewHtml"])
