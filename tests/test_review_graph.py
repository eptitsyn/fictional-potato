import asyncio

from app.langchain_integration import review_graph


class FakeChain:
    def __init__(self, responses):
        self._responses = iter(responses)

    async def ainvoke(self, _messages):
        return next(self._responses)


def test_run_graph_accepts_review_guidance_without_name_error():
    chain = FakeChain(
        [
            "Краткий план ревью.",
            '[{"file_path":"app/example.py","start_line":12,"end_line":15,"severity":"warning","comment":"Проверь границы диапазона."}]',
            "[]",
            '[{"file_path":"app/example.py","start_line":12,"end_line":15,"severity":"warning","comment":"Проверь границы диапазона."}]',
        ]
    )

    result = asyncio.run(
        review_graph._run_graph(
            llm=None,
            chain=chain,
            parser=None,
            state={
                "diff": "--- a/app/example.py\n+++ b/app/example.py\n@@ -12,4 +12,4 @@",
                "metadata": {},
                "system_prompt": "Ты опытный ревьюер кода.",
                "plan": "",
                "security_comments": [],
                "quality_comments": [],
                "final_comments": [],
            },
            metadata={},
            review_guidance="Используй диапазоны строк, если замечание относится к нескольким строкам.",
            max_context_tokens=4000,
            plan_context_tokens=128,
            graph_response_tokens=256,
        )
    )

    assert result == [
        {
            "file_path": "app/example.py",
            "line_number": 12,
            "line_end": 15,
            "severity": "warning",
            "comment_body": "Проверь границы диапазона.",
        }
    ]
