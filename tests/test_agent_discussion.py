import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from xianyu_agent.agent_registry import AgentRegistry
from xianyu_agent.agents.base import BaseAgent


class ScriptedDiscussionAgent:
    def __init__(self, name, prefix):
        self.name = name
        self.prefix = prefix
        self.seen_transcript_lengths = []

    def contribute_to_discussion(self, topic, transcript, context=None):
        self.seen_transcript_lengths.append(len(transcript))
        return f"{self.prefix}:{topic}:{len(transcript)}"


class SilentBaseAgent(BaseAgent):
    def chat(self, system, user, **kwargs):
        return ""


class SilentListingAgent(SilentBaseAgent):
    name = "listing"


class SilentDiscoveryAgent(SilentBaseAgent):
    name = "discovery"


class SilentAnalyticsAgent(SilentBaseAgent):
    name = "analytics"


class AgentDiscussionTests(unittest.TestCase):
    def test_discussion_runs_round_robin_and_records_transcript(self):
        registry = AgentRegistry()
        listing = ScriptedDiscussionAgent("listing", "draft")
        analytics = ScriptedDiscussionAgent("analytics", "price")
        registry.register_agent("listing", listing)
        registry.register_agent("analytics", analytics)

        discussion = registry.discuss(
            topic="如何上架蓝牙耳机",
            participants=["listing", "analytics"],
            rounds=2,
            context={"price": 98},
        )

        self.assertEqual(discussion["topic"], "如何上架蓝牙耳机")
        self.assertEqual(discussion["participants"], ["listing", "analytics"])
        self.assertEqual([turn["agent"] for turn in discussion["transcript"]], ["listing", "analytics", "listing", "analytics"])
        self.assertEqual(listing.seen_transcript_lengths, [0, 2])
        self.assertEqual(analytics.seen_transcript_lengths, [1, 3])
        self.assertIn("analytics", discussion["summary"])

        history = registry.get_discussion_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["discussion_id"], discussion["discussion_id"])

    def test_discussion_history_persists_between_instances(self):
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "agent_collaboration.jsonl"
            registry = AgentRegistry(history_path=history_path)
            registry.register_agent("listing", ScriptedDiscussionAgent("listing", "draft"))
            registry.register_agent("analytics", ScriptedDiscussionAgent("analytics", "price"))

            discussion = registry.discuss(
                topic="collaborative listing",
                participants=["listing", "analytics"],
                rounds=1,
                context={"price": 98},
            )

            reloaded = AgentRegistry(history_path=history_path)
            history = reloaded.get_discussion_history()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["discussion_id"], discussion["discussion_id"])
            self.assertEqual(history[0]["summary"], discussion["summary"])
            self.assertEqual([turn["agent"] for turn in history[0]["transcript"]], ["listing", "analytics"])

    def test_missing_participant_is_recorded_as_failed_turn(self):
        registry = AgentRegistry()

        discussion = registry.discuss("讨论库存策略", ["missing"], rounds=1)

        self.assertEqual(discussion["transcript"][0]["agent"], "missing")
        self.assertEqual(discussion["transcript"][0]["status"], "failed")
        self.assertIn("not registered", discussion["transcript"][0]["content"])

    def test_base_agent_discussion_fallback_is_role_specific(self):
        registry = AgentRegistry()
        registry.register_agent("listing", SilentListingAgent())
        registry.register_agent("discovery", SilentDiscoveryAgent())
        registry.register_agent("analytics", SilentAnalyticsAgent())

        discussion = registry.discuss("如何上架蓝牙耳机", ["listing", "discovery", "analytics"], rounds=1)
        contents = [turn["content"] for turn in discussion["transcript"]]

        self.assertIn("上架", contents[0])
        self.assertIn("市场", contents[1])
        self.assertIn("利润", contents[2])

    def test_discussion_routes_are_registered(self):
        from xianyu_agent.server import app

        routes = {(route.path, tuple(sorted(route.methods or []))) for route in app.routes}

        self.assertIn(("/agents/discuss", ("POST",)), routes)
        self.assertIn(("/agents/discussions", ("GET",)), routes)


if __name__ == "__main__":
    unittest.main()
