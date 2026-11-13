"""Lets older tests keep faking the model as `app.client.models.generate_content[_stream]`.

`install(A)` routes llm.generate/llm.stream into that fake and adapts the
legacy-shaped reply objects back into llm Result/Chunk values.
"""
from types import SimpleNamespace
import llm


def _config(system, tools, temperature, json_schema):
    return SimpleNamespace(system_instruction=system, tools=tools, temperature=temperature,
                           response_json_schema=json_schema)


def _usage(u):
    if not u:
        return None
    return {"prompt": u.prompt_token_count, "reply": u.candidates_token_count, "total": u.total_token_count}


# What the fake model reports as having answered; a test sets this to simulate a fallback.
SERVED = llm._served("gemini", "gemini-test")


def install(A):
    def generate(contents, system=None, tools=None, temperature=None, json_schema=None, max_tokens=None, **_):
        r = A.client.models.generate_content(contents=contents,
                                             config=_config(system, tools, temperature, json_schema))
        return llm.Result((getattr(r, "text", "") or "").strip(), None, _usage(getattr(r, "usage_metadata", None)),
                          served=SERVED)

    def stream(contents, system=None, tools=None, temperature=None, max_tokens=None, **_):
        for ch in A.client.models.generate_content_stream(contents=contents,
                                                          config=_config(system, tools, temperature, None)):
            text = "".join(p.text for cand in ch.candidates for p in cand.content.parts)
            yield llm.Chunk(text, None, _usage(getattr(ch, "usage_metadata", None)), served=SERVED)

    llm.generate, llm.stream = generate, stream
