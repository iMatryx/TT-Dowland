"""Offline preparation-only comparison; no bot, network or Telegram credentials.

Run: .venv/bin/python -m scripts.benchmark_preparation
A missing verification receipt models the previous redundant source probe.
"""
import asyncio
from collections import defaultdict
import json
import logging
from pathlib import Path
import platform
import statistics
import subprocess
import tempfile

from bot.services.media import verify_media
from bot.services.preview import create_preview


class Measurements(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, record):
        event = json.loads(record.getMessage())
        if event.get('event') == 'performance_stage':
            self.events.append(event)


async def main():
    recorder = Measurements()
    logger = logging.getLogger('bot.performance')
    logger.setLevel(logging.INFO)
    logger.addHandler(recorder)
    samples = defaultdict(list)
    try:
        with tempfile.TemporaryDirectory(prefix='preparation-benchmark-') as temporary:
            root = Path(temporary)
            mkv, mp4 = root / 'source.mkv', root / 'source.mp4'
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=640x360:rate=30:duration=10', '-f', 'lavfi', '-i',
                'sine=frequency=440:duration=10', '-c:v', 'libx264', '-preset', 'veryfast',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-threads', '1', '-shortest', str(mkv)], check=True)
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(mkv),
                '-c', 'copy', '-movflags', '+faststart', str(mp4)], check=True)
            for source in (mp4, mkv):
                verified = await verify_media(source)
                # Warm both paths, then alternate order to reduce order bias.
                for iteration in range(8):
                    for reuse in ((False, True) if iteration % 2 == 0 else (True, False)):
                        recorder.events.clear()
                        output = root / 'prepared.mp4'
                        await create_preview(source, output, max_bytes=50_000_000, timeout=30,
                                             verified=verified if reuse else None)
                        if iteration:
                            events = recorder.events
                            samples[source.suffix, reuse].append({
                                name: sum(e['duration_seconds'] for e in events if e['stage'] == name)
                                for name in ('verification', 'conversion', 'queue_wait')})
                        output.unlink(missing_ok=True)
            result = {'conditions': {'system': platform.system(), 'machine': platform.machine(),
                'source': 'synthetic 10s 640x360 30fps H.264/AAC', 'samples_per_case': 7,
                'includes': 'preparation only; warm local files; serial; existing stage instrumentation',
                'excludes': 'initial download verification, analysis, network, Telegram upload'}, 'cases': []}
            for (container, reuse), rows in samples.items():
                result['cases'].append({'container': container, 'reuse_verification': reuse,
                    'median_seconds': {name: round(statistics.median(r[name] for r in rows), 6)
                                       for name in rows[0]}})
            print(json.dumps(result, indent=2))
    finally:
        logger.removeHandler(recorder)


if __name__ == '__main__':
    asyncio.run(main())
