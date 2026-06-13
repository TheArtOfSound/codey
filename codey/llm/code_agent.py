"""CodeAgent — LLM-powered code operations with full structural awareness."""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

# import anthropic  # Replaced with provider system

if TYPE_CHECKING:
    from codey.graph.engine import CodebaseGraph
    from codey.llm.prompt_builder import PromptBuilder
    from codey.nfet.sweep import NFETSweep, SweepResult

logger = logging.getLogger(__name__)

_LLM_CALL_TIMEOUT_SECONDS = 120


class CodeAgent:
    """Handles LLM-powered code generation, refactoring, and impact analysis.

    Every operation is informed by the live NFET structural state of the
    codebase, so the model can avoid destabilising high-stress regions.
    """

    def __init__(
        self,
        graph: CodebaseGraph,
        sweep_engine: NFETSweep,
        model: str = "claude-sonnet-4-6",
        api_key: str | None = None,
    ) -> None:
        self.graph = graph
        self.sweep_engine = sweep_engine
        self.model = model

        from codey.llm.prompt_builder import PromptBuilder

        self.prompt_builder = PromptBuilder(graph, sweep_engine)

        # Use provider system instead of direct Anthropic
        pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_code(self, request: str, target_file: str | None = None) -> dict[str, Any]:
        """Generate code for a user request with full structural context.

        Returns
        -------
        dict with keys:
            code: str — the generated code
            explanation: str — model's reasoning
            structural_impact: dict — estimated stress/coupling deltas and recommendation
        """
        sweep_result = self.sweep_engine.run(self.graph)
        system, messages = self.prompt_builder.build_full_prompt(
            user_request=self._wrap_generation_request(request, target_file),
            sweep_result=sweep_result,
            target_file=target_file,
        )
        raw = self._call_llm(system, messages)
        return self._parse_generation_response(raw, sweep_result, target_file)

    def suggest_refactor(self, component_id: str) -> dict[str, Any]:
        """Suggest refactorings to reduce stress on a component.

        Returns
        -------
        dict with keys:
            suggestions: list[str]
            estimated_improvement: dict
        """
        stress = self.graph.stress_score(component_id)
        coupling = self.graph.coupling_score(component_id)
        comp_data = self.graph._graph.nodes.get(component_id)
        file_path = comp_data.get("file_path", component_id) if comp_data else component_id
        cohesion = self.graph.cohesion_score(file_path)
        cascade = self.graph.cascade_depth(component_id)
        bc = self.graph.betweenness_centrality().get(component_id, 0.0)

        sweep_result = self.sweep_engine.run(self.graph)
        context = self.prompt_builder.build_context(sweep_result, target_file=file_path)

        request = (
            f"Suggest concrete refactorings to reduce the stress of component "
            f"'{component_id}' (file: {file_path}).\n\n"
            f"Current metrics:\n"
            f"  Stress: {stress:.2f}\n"
            f"  Coupling: {coupling:.2f}\n"
            f"  Cohesion: {cohesion:.2f}\n"
            f"  Cascade depth: {cascade}\n"
            f"  Betweenness centrality: {bc:.2f}\n\n"
            f"Respond in JSON with keys: suggestions (list of strings), "
            f"estimated_improvement (dict with stress_delta, coupling_delta, cohesion_delta)."
        )

        system = self.prompt_builder.build_system_prompt()
        messages = [
            {"role": "user", "content": f"{context}\n\nUSER REQUEST: {request}"},
        ]

        raw = self._call_llm(system, messages)
        return self._parse_refactor_response(raw)

    def analyze_change_impact(self, file_path: str, proposed_diff: str) -> dict[str, Any]:
        """Analyze the structural impact of a proposed code change.

        Returns
        -------
        dict with keys:
            impact_summary: str
            risk_level: str — "low", "moderate", or "high"
            affected_components: list[str]
            recommendation: str
        """
        sweep_result = self.sweep_engine.run(self.graph)
        context = self.prompt_builder.build_context(sweep_result, target_file=file_path)

        request = (
            f"Analyze the structural impact of this proposed change to {file_path}.\n\n"
            f"```diff\n{proposed_diff}\n```\n\n"
            f"Consider:\n"
            f"1. Will this increase coupling to high-stress components?\n"
            f"2. How many components could be affected by cascading failures?\n"
            f"3. Does this move the codebase toward or away from the stability ridge?\n\n"
            f"Respond in JSON with keys: impact_summary (str), risk_level (str: low/moderate/high), "
            f"affected_components (list of file paths), recommendation (str)."
        )

        system = self.prompt_builder.build_system_prompt()
        messages = [
            {"role": "user", "content": f"{context}\n\nUSER REQUEST: {request}"},
        ]

        raw = self._call_llm(system, messages)
        return self._parse_impact_response(raw)

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm(self, system: str, messages: list[dict[str, str]]) -> str:
        """Make an API call via the multi-provider system."""
        import asyncio
        from codey.saas.intelligence.providers import call_model, resolve_model

        provider, model = resolve_model("code_generation")
        api_messages = [{"role": "system", "content": system}] + messages

        async def _run_call() -> str:
            try:
                return await asyncio.wait_for(
                    call_model(provider, model, api_messages, max_tokens=4096),
                    timeout=_LLM_CALL_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"LLM call timed out after {_LLM_CALL_TIMEOUT_SECONDS}s"
                ) from exc

        # Handle both sync and async contexts
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're in an async context — create a new task
            import concurrent.futures
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = pool.submit(asyncio.run, _run_call())
            try:
                return future.result(timeout=_LLM_CALL_TIMEOUT_SECONDS + 5)
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                raise TimeoutError(
                    f"LLM call timed out after {_LLM_CALL_TIMEOUT_SECONDS}s"
                ) from exc
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        else:
            return asyncio.run(_run_call())

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_refactor_response(self, raw: object) -> dict[str, Any]:
        """Parse a refactor response while preserving the public dict shape."""
        parsed = self._parse_json_response(raw, fallback=None)
        defaults = {
            "stress_delta": 0.0,
            "coupling_delta": 0.0,
            "cohesion_delta": 0.0,
        }

        if isinstance(parsed, dict):
            suggestions = parsed.get("suggestions", [])
            if isinstance(suggestions, list):
                parsed["suggestions"] = [
                    self._coerce_response_text(suggestion)
                    for suggestion in suggestions
                ]
            else:
                parsed["suggestions"] = [self._coerce_response_text(suggestions)]

            improvement = parsed.get("estimated_improvement")
            if isinstance(improvement, dict):
                for key, value in defaults.items():
                    improvement.setdefault(key, value)
            else:
                parsed["estimated_improvement"] = defaults.copy()
            return parsed

        return {
            "suggestions": [self._coerce_response_text(raw)],
            "estimated_improvement": defaults,
        }

    def _parse_impact_response(self, raw: object) -> dict[str, Any]:
        """Parse an impact response while preserving the public dict shape."""
        parsed = self._parse_json_response(raw, fallback=None)
        if isinstance(parsed, dict):
            parsed["impact_summary"] = self._coerce_response_text(
                parsed.get("impact_summary", "")
            )
            parsed["risk_level"] = self._coerce_response_text(
                parsed.get("risk_level", "moderate")
            )
            affected_components = parsed.get("affected_components", [])
            if isinstance(affected_components, list):
                parsed["affected_components"] = [
                    self._coerce_response_text(component)
                    for component in affected_components
                ]
            else:
                parsed["affected_components"] = [
                    self._coerce_response_text(affected_components)
                ]
            parsed["recommendation"] = self._coerce_response_text(
                parsed.get(
                    "recommendation",
                    "Unable to parse structured response. Review the raw analysis above.",
                )
            )
            return parsed

        return {
            "impact_summary": self._coerce_response_text(raw),
            "risk_level": "moderate",
            "affected_components": [],
            "recommendation": "Unable to parse structured response. Review the raw analysis above.",
        }

    def _wrap_generation_request(self, request: str, target_file: str | None) -> str:
        """Wrap a user request with output formatting instructions."""
        parts = [request]
        if target_file:
            parts.append(f"\nTarget file: {target_file}")
        parts.append(
            "\n\nRespond in JSON with keys:\n"
            "  code (str): the generated code\n"
            "  explanation (str): your reasoning about correctness and structural impact\n"
            "  structural_impact (dict with keys: estimated_stress_delta (float), "
            "estimated_coupling_delta (float), recommendation (str))"
        )
        return "\n".join(parts)

    def _parse_generation_response(
        self,
        raw: object,
        sweep_result: SweepResult,
        target_file: str | None,
    ) -> dict[str, Any]:
        """Parse the LLM's generation response into a structured dict."""
        parsed = self._parse_json_response(raw, fallback=None)
        if isinstance(parsed, dict) and "code" in parsed:
            # Ensure all expected keys are present
            parsed["code"] = self._coerce_response_text(parsed["code"])
            parsed.setdefault("explanation", "")
            parsed.setdefault("structural_impact", {
                "estimated_stress_delta": 0.0,
                "estimated_coupling_delta": 0.0,
                "recommendation": "No structured impact estimate available.",
            })
            return parsed

        # Fallback: treat the entire response as code with no structured metadata
        return {
            "code": self._coerce_response_text(raw),
            "explanation": "Response was not in structured JSON format.",
            "structural_impact": {
                "estimated_stress_delta": 0.0,
                "estimated_coupling_delta": 0.0,
                "recommendation": "Unable to estimate structural impact from unstructured response.",
            },
        }

    @staticmethod
    def _coerce_response_text(raw: object) -> str:
        """Convert provider response fragments into safe text for code fields."""
        if isinstance(raw, str):
            return raw
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        if raw is None:
            return ""
        if isinstance(raw, (dict, list)):
            try:
                return json.dumps(raw, default=str)
            except (TypeError, ValueError):
                return str(raw)
        return str(raw)

    @staticmethod
    def _parse_json_response(raw: object, fallback: Any) -> Any:
        """Attempt to extract and parse a JSON object from the LLM's response.

        Handles responses where JSON is wrapped in markdown code fences.
        Returns the parsed dict on success, or fallback on failure.
        """
        if isinstance(raw, (dict, list)):
            return raw
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace").strip()
        elif isinstance(raw, str):
            text = raw.strip()
        else:
            return fallback

        # Strip markdown code fences if present
        if text.startswith("```"):
            # Remove opening fence (with optional language tag)
            first_newline = text.index("\n") if "\n" in text else len(text)
            text = text[first_newline + 1:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        # Try to find a JSON object in the text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        # Try parsing the whole thing
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return fallback
