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


def install(A):
    def generate(model, contents, system=None, tools=None, temperature=None, json_schema=None):
        r = A.client.models.generate_content(model=model, contents=contents,
                                             config=_config(system, tools, temperature, json_schema))
        return llm.Result((getattr(r, "text", "") or "").strip(), None, _usage(getattr(r, "usage_metadata", None)))

    def stream(model, contents, system=None, tools=None, temperature=None):
        for ch in A.client.models.generate_content_stream(model=model, contents=contents,
                                                          config=_config(system, tools, temperature, None)):
            text = "".join(p.text for cand in ch.candidates for p in cand.content.parts)
            yield llm.Chunk(text, None, _usage(getattr(ch, "usage_metadata", None)))

    llm.generate, llm.stream = generate, stream
