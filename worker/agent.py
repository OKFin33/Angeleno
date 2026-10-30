"""Angeleno LiveKit worker. The shared core owns all actor context and memory.

Transport wiring derived from talk-to-fengge (Apache-2.0); see NOTICE.
"""
from functools import partial
import os

from livekit.agents import Agent, AgentServer, JobExecutorType, cli

from her_agent.config import load_env
from her_agent.voice import start_voice_session, validate_voice_setup
from worker.audio_budget import configured_allowance
from worker.energy_vad import EnergyVAD
from worker.paraformer_stt import ParaformerSTT
from worker.voxcpm_tts import VoxCPMHttpTTS


class VoiceAgent(Agent):
    def __init__(self, instructions, *, llm_instance, env):
        super().__init__(
            instructions=instructions,
            llm=llm_instance,
            stt=ParaformerSTT(
                api_key=env['DASHSCOPE_API_KEY'],
                url=env.get('DASHSCOPE_WS_URL'),
                allowance=configured_allowance(env),
                paid_enabled=env.get('ANGELENO_PAID_ENABLED') == '1',
            ),
            tts=VoxCPMHttpTTS(
                url=env['VOXCPM_URL'], voice='jo',
                paid_enabled=env.get('ANGELENO_PAID_ENABLED') == '1',
            ),
            vad=EnergyVAD(speech_threshold=500, silence_threshold=200,
                          min_speech_duration=0.4, min_silence_duration=0.5),
        )


def main():
    env = load_env()
    # The runner performs the same check before spawning any process. Keep this
    # check for direct `python -m worker.main start` as well.
    from scripts.run_voice import check_configuration
    errors = check_configuration(env, starting=True)
    if errors:
        raise ValueError('; '.join(errors))
    validate_voice_setup(env)
    os.environ.update(env)
    server = AgentServer(job_executor_type=JobExecutorType.THREAD,
                         initialize_process_timeout=60,
                         port=int(env.get('LIVEKIT_WORKER_PORT', '8081')))

    @server.rtc_session(agent_name=env.get('ANGELENO_AGENT_NAME', 'angeleno-jo'))
    async def entrypoint(ctx):
        await ctx.connect()
        await start_voice_session(ctx, partial(VoiceAgent, env=env), env)

    cli.run_app(server)
