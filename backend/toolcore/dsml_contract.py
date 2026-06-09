from __future__ import annotations

# Shared DSML/XML format example lines used by both prompt_contract and
# task_session builders.  The list is the canonical source — both modules
# import and embed these lines verbatim so the tool-call format guidance
# stays consistent without duplicated literals.

DSML_TOOL_CALLS_FORMAT: list[str] = [
    "<|DSML|tool_calls>",
    '  <|DSML|invoke name="TOOL_NAME_HERE">',
    '    <|DSML|parameter name="PARAMETER_NAME"><![CDATA[PARAMETER_VALUE]]></|DSML|parameter>',
    "  </|DSML|invoke>",
    "</|DSML|tool_calls>",
]
