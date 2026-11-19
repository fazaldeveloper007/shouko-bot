"""Actual pinned receiver+codec+player with fake gateway, no network."""
import json
import pathlib
import subprocess
import unittest
from test_voice_live_duplex import PREFIX, BODY
ROOT=pathlib.Path(__file__).resolve().parents[1]

GROUP_PREFIX=PREFIX.replace("root+'/voice_live/worker_duplex.cjs'", "root+'/voice_live/worker_group.cjs'")
GROUP_PREFIX=GROUP_PREFIX.replace("const sandbox={Buffer,", "const sandbox={Buffer,__dirname:root+'/voice_live',")
GROUP_PREFIX=GROUP_PREFIX.replace("if(name==='./speech_gate.cjs')", "if(name==='node:worker_threads'||name==='node:path')return require(name);if(name==='./group_gate.cjs')return require(root+'/voice_live/group_gate.cjs');if(name==='./speech_gate.cjs')")
GROUP_PREFIX=GROUP_PREFIX.replace("const target='400000000000000',ssrc=12345", "const second='600000000000000';const target='400000000000000',ssrc=12345")
GROUP_PREFIX=GROUP_PREFIX.replace("conn.receiver.ssrcMap.update({userId:target,audioSSRC:ssrc});", "conn.receiver.ssrcMap.update({userId:target,audioSSRC:ssrc});conn.receiver.ssrcMap.update({userId:second,audioSSRC:ssrc+1});")
GROUP_PREFIX=GROUP_PREFIX.replace("user:'300000000000000',target}", "user:'300000000000000',targets:[target,second]}")
GROUP_BODY=BODY.replace('assert.deepEqual(subscriptions,[target]);', 'assert.deepEqual(subscriptions,[target,second]);')
GROUP_BODY=GROUP_BODY.replace('await new Promise(setImmediate);\n }', 'await new Promise(r=>setTimeout(r,15));\n }')
GROUP_BODY=GROUP_BODY.replace('async function inputPacket(silent=false){', "async function inputPacket(silent=false,actor=target){")
GROUP_BODY=GROUP_BODY.replace('header.writeUInt32BE(ssrc,8);', 'header.writeUInt32BE(actor===target?ssrc:ssrc+1,8);')
GROUP_BODY=GROUP_BODY.replace("else if(mode==='barge')for(let i=0;i<8;i++)await inputPacket();", "else if(mode==='barge')for(let i=0;i<8;i++)await inputPacket(false,second);")
GROUP_BODY=GROUP_BODY.replace("assert.equal(messages.filter(m=>m.type==='speech_start').length,2);", "assert.equal(messages.filter(m=>m.type==='speech_start').length,2);assert.equal(messages.filter(m=>m.type==='speech_start')[1].speaker,mode==='barge'?second:target);")

class GroupWorkerTests(unittest.TestCase):
    def test_actual_group_receive_playback_barge_deadline_and_safety(self):
        for mode, code in [('barge','STOPPED'), ('deadline','SESSION_LIMIT'), ('drainbarge','SESSION_LIMIT'), ('member','OTHER_SPEAKER'), ('dave','DAVE_NOT_READY'), ('stop','STOPPED')]:
            with self.subTest(mode=mode):
                p=subprocess.run(['node','-e',GROUP_PREFIX+GROUP_BODY,str(ROOT),mode],capture_output=True,text=True,timeout=6)
                self.assertEqual(p.returncode,0,p.stderr)
                result=json.loads(p.stdout)
                self.assertEqual(result['result']['code'],code)
                if mode=='barge':
                    self.assertEqual(result['result']['turnsCompleted'],1)
                    self.assertEqual(result['result']['droppedChunks'],2)
                    self.assertGreater(result['nonzero'],0)
