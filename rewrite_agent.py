import json
with open("services/diagnosis-agent/agent.py", "r") as f:
    content = f.read()

# Replace import
content = content.replace("import anthropic", "from google import genai\nfrom google.genai import types")

# Replace initialization
content = content.replace("self.llm = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)", "self.llm = genai.Client(api_key=settings.gemini_api_key)")

# Now we need to replace the agentic loop. We'll use regex or string split.
import re

agentic_loop_old = """        # The conversation history for the agentic loop
        messages = [{"role": "user", "content": user_prompt}]

        tool_call_count = 0

        try:
            # ── AGENTIC LOOP ──────────────────────────────────────────
            while tool_call_count < MAX_TOOL_CALLS:
                log.info(
                    "llm_call",
                    incident_id=incident.incident_id,
                    loop_iteration=tool_call_count,
                )

                response = await self.llm.messages.create(
                    model=settings.anthropic_model,
                    system=DIAGNOSIS_SYSTEM_PROMPT,
                    messages=messages,
                    tools=DIAGNOSTIC_TOOLS,
                    temperature=AGENT_TEMPERATURE,
                    max_tokens=MAX_TOKENS,
                )

                # Check stop reason
                if response.stop_reason == "end_turn":
                    # Claude is done — extract the final diagnosis
                    final_text = self._extract_text(response)
                    diagnosis = self._parse_diagnosis(final_text)
                    incident = self._apply_diagnosis(incident, diagnosis)
                    log.info(
                        "diagnosis_completed",
                        incident_id=incident.incident_id,
                        root_cause=incident.root_cause[:100] if incident.root_cause else "none",
                        evidence_count=len(incident.evidence),
                        tool_calls=tool_call_count,
                    )
                    return incident

                elif response.stop_reason == "tool_use":
                    # Claude wants to call tools — execute them
                    # First, add Claude's response (with tool_use blocks) to messages
                    messages.append({"role": "assistant", "content": response.content})

                    # Process each tool_use block
                    tool_results = []
                    for block in response.content:
                        if block.type == "tool_use":
                            tool_call_count += 1
                            tool_name = block.name
                            tool_input = block.input

                            log.info(
                                "tool_call",
                                incident_id=incident.incident_id,
                                tool=tool_name,
                                input_keys=list(tool_input.keys()),
                                call_number=tool_call_count,
                            )

                            # Execute the tool against the MCP server
                            result = await execute_tool(tool_name, tool_input)

                            # Store as evidence on the incident
                            source = TOOL_SOURCE.get(tool_name, "unknown")
                            incident.add_evidence(
                                source=source,
                                tool=tool_name,
                                content=result[:2000],  # Cap evidence size
                                relevance=f"Called by diagnosis agent during investigation (call #{tool_call_count})",
                            )

                            # Add tool result for Claude
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result[:4000],  # Cap for token limits
                            })

                    # Send all tool results back to Claude
                    messages.append({"role": "user", "content": tool_results})

                else:
                    # Unexpected stop reason — extract whatever we can
                    log.warning(
                        "unexpected_stop_reason",
                        stop_reason=response.stop_reason,
                        incident_id=incident.incident_id,
                    )
                    final_text = self._extract_text(response)
                    diagnosis = self._parse_diagnosis(final_text)
                    incident = self._apply_diagnosis(incident, diagnosis)
                    return incident

            # ── MAX TOOL CALLS REACHED ────────────────────────────────
            log.warning(
                "max_tool_calls_reached",
                incident_id=incident.incident_id,
                tool_calls=tool_call_count,
            )
            # Ask Claude for a final verdict with what it has
            messages.append({
                "role": "user",
                "content": (
                    "You've reached the maximum number of tool calls. "
                    "Based on the evidence collected so far, provide your best "
                    "diagnosis as JSON now."
                ),
            })
            response = await self.llm.messages.create(
                model=settings.anthropic_model,
                system=DIAGNOSIS_SYSTEM_PROMPT,
                messages=messages,
                temperature=AGENT_TEMPERATURE,
                max_tokens=MAX_TOKENS,
            )
            final_text = self._extract_text(response)"""

agentic_loop_new = """        # The conversation history for the agentic loop
        messages = [{"role": "user", "parts": [{"text": user_prompt}]}]

        tool_call_count = 0

        try:
            # ── AGENTIC LOOP ──────────────────────────────────────────
            while tool_call_count < MAX_TOOL_CALLS:
                log.info(
                    "llm_call",
                    incident_id=incident.incident_id,
                    loop_iteration=tool_call_count,
                )

                response = await self.llm.aio.models.generate_content(
                    model=settings.gemini_model,
                    contents=messages,
                    config=types.GenerateContentConfig(
                        system_instruction=DIAGNOSIS_SYSTEM_PROMPT,
                        tools=DIAGNOSTIC_TOOLS,
                        temperature=AGENT_TEMPERATURE,
                        max_output_tokens=MAX_TOKENS,
                    )
                )

                if not response.function_calls:
                    # Gemini is done — extract the final diagnosis
                    final_text = response.text
                    diagnosis = self._parse_diagnosis(final_text)
                    incident = self._apply_diagnosis(incident, diagnosis)
                    log.info(
                        "diagnosis_completed",
                        incident_id=incident.incident_id,
                        root_cause=incident.root_cause[:100] if incident.root_cause else "none",
                        evidence_count=len(incident.evidence),
                        tool_calls=tool_call_count,
                    )
                    return incident
                else:
                    # Gemini wants to call tools — execute them
                    if response.candidates and response.candidates[0].content:
                        messages.append(response.candidates[0].content)

                    # Process each function call
                    tool_parts = []
                    for call in response.function_calls:
                        tool_call_count += 1
                        tool_name = call.name
                        
                        # call.args is a protobuf Struct, convert to dict
                        tool_input = dict(call.args) if call.args else {}

                        log.info(
                            "tool_call",
                            incident_id=incident.incident_id,
                            tool=tool_name,
                            input_keys=list(tool_input.keys()),
                            call_number=tool_call_count,
                        )

                        # Execute the tool against the MCP server
                        result = await execute_tool(tool_name, tool_input)

                        # Store as evidence on the incident
                        source = TOOL_SOURCE.get(tool_name, "unknown")
                        incident.add_evidence(
                            source=source,
                            tool=tool_name,
                            content=result[:2000],  # Cap evidence size
                            relevance=f"Called by diagnosis agent during investigation (call #{tool_call_count})",
                        )

                        # Add tool result for Gemini
                        tool_parts.append(
                            types.Part.from_function_response(
                                name=tool_name,
                                response={"result": result[:4000]}
                            )
                        )

                    # Send all tool results back to Gemini
                    messages.append({"role": "user", "parts": tool_parts})

            # ── MAX TOOL CALLS REACHED ────────────────────────────────
            log.warning(
                "max_tool_calls_reached",
                incident_id=incident.incident_id,
                tool_calls=tool_call_count,
            )
            # Ask Gemini for a final verdict with what it has
            messages.append({
                "role": "user",
                "parts": [{"text": (
                    "You've reached the maximum number of tool calls. "
                    "Based on the evidence collected so far, provide your best "
                    "diagnosis as JSON now."
                )}]
            })
            response = await self.llm.aio.models.generate_content(
                model=settings.gemini_model,
                contents=messages,
                config=types.GenerateContentConfig(
                    system_instruction=DIAGNOSIS_SYSTEM_PROMPT,
                    temperature=AGENT_TEMPERATURE,
                    max_output_tokens=MAX_TOKENS,
                )
            )
            final_text = response.text"""

content = content.replace(agentic_loop_old, agentic_loop_new)

# Remove unused _extract_text function
extract_old = """    def _extract_text(self, response) -> str:
        \"\"\"Extract text content from Claude's response.\"\"\"
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""

"""
content = content.replace(extract_old, "")
content = content.replace("Claude", "Gemini")

with open("services/diagnosis-agent/agent.py", "w") as f:
    f.write(content)

