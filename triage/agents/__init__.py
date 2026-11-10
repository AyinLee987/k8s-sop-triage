from triage.agents.code_sop import CodeSopAgent
from triage.agents.rule_baseline import RuleBaselineAgent
from triage.agents.text_sop import TextSopAgent

ARMS = {"rule": RuleBaselineAgent, "text": TextSopAgent, "code": CodeSopAgent}

__all__ = ["ARMS", "RuleBaselineAgent", "TextSopAgent", "CodeSopAgent"]
