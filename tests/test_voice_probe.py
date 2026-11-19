"""Offline privacy gates and real Opus decode, without Discord/AI access."""
import asyncio
import importlib.util
import json
import pathlib
import shutil
import subprocess
import types
import unittest
from unittest.mock import patch, AsyncMock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('receive_probe_test', ROOT / 'voice_probe/receive.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Guards(unittest.TestCase):
    def channel(self, ids):
        ch = types.SimpleNamespace(id=10)
        ch.voice_states = {i: types.SimpleNamespace(channel=ch) for i in ids}
        ch.guild = types.SimpleNamespace(voice_channels=[ch])
        return ch

    def test_target_alone_or_with_bot_only(self):
        self.assertTrue(probe.safe_members(self.channel([42]), 42, 99))
        self.assertTrue(probe.safe_members(self.channel([42, 99]), 42, 99))

    def test_reject_any_other_member_even_if_not_cached(self):
        self.assertFalse(probe.safe_members(self.channel([42, 77]), 42, 99))
        self.assertFalse(probe.safe_members(self.channel([42, 99, 77]), 42, 99))

    def test_requester_must_remain_present(self):
        self.assertFalse(probe.safe_members(self.channel([99]), 42, 99))

    def test_identity_not_username_or_confusable_digits(self):
        self.assertEqual(probe.snowflake('1217032327260274773'), 1217032327260274773)
        for value in ('Fazal', '123', '１２１７０３２３２７２６０２７４７７３', '-1217032327260274773'):
            with self.assertRaises(Exception):
                probe.snowflake(value)

    def test_worker_and_preflight_do_not_inherit_credentials(self):
        with patch.dict(probe.os.environ, {'DISCORD_TOKEN': 'DUMMY', 'GEMINI_API_KEY': 'DUMMY',
                                         'GOOGLE_APPLICATION_CREDENTIALS': 'DUMMY', 'PATH': '/bin'}):
            env = probe.worker_env()
        self.assertNotIn('DISCORD_TOKEN', env)
        self.assertNotIn('GEMINI_API_KEY', env)
        self.assertNotIn('GOOGLE_APPLICATION_CREDENTIALS', env)
        self.assertEqual(env['PATH'], '/bin')

    def test_background_launch_disabled_before_token_read(self):
        with patch.object(probe.sys.stdin, 'isatty', return_value=False):
            with self.assertRaises(RuntimeError):
                asyncio.run(probe.run(42))


class GatewayGuards(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_request_is_exact_and_membership_gated(self):
        channel = Guards().channel([42])
        channel.guild.id = 100
        channel.guild.change_voice_state = AsyncMock()
        client = types.SimpleNamespace(user=types.SimpleNamespace(id=99))
        bridge = probe.ProbeBridge(client, channel, 42)
        bridge.state = 'joining'
        bridge.write = AsyncMock()
        payload = {'op': 4, 'd': {'guild_id': '100', 'channel_id': '10'}}
        await bridge.dispatch({'type': 'gateway_send', 'id': 1, 'payload': payload})
        channel.guild.change_voice_state.assert_awaited_once_with(channel=channel, self_deaf=False, self_mute=True)
        self.assertTrue(bridge.write.await_args.args[0]['ok'])
        channel.guild.change_voice_state.reset_mock()
        channel.voice_states[77] = types.SimpleNamespace(channel=channel)
        await bridge.dispatch({'type': 'gateway_send', 'id': 2, 'payload': payload})
        channel.guild.change_voice_state.assert_not_awaited()
        self.assertFalse(bridge.write.await_args.args[0]['ok'])
        channel.voice_states.pop(77)
        payload['d']['channel_id'] = '999'
        await bridge.dispatch({'type': 'gateway_send', 'id': 3, 'payload': payload})
        channel.guild.change_voice_state.assert_not_awaited()
        self.assertFalse(bridge.write.await_args.args[0]['ok'])


HARNESS = r'''
const fs=require('node:fs'), vm=require('node:vm'), E=require('node:events');
const root=process.argv[1], scenario=process.argv[2];
const O=require(root+'/voice_probe/node_modules/opusscript');
const input=new E(), speaking=new E(), stream=new E(), connection=new E();
const messages=[], subscribed=[], timers=[];let wiped=false, destroyed=false;
stream.destroy=()=>{destroyed=true;};connection.destroy=()=>{};
connection.receiver={speaking,subscribe:(id)=>{subscribed.push(id);return stream;}};
const voice={VoiceConnectionStatus:{Ready:'ready'},EndBehaviorType:{Manual:0},joinVoiceChannel:()=>connection};
let encrypted=true;
const sandbox={Buffer,require:name=>{
 if(name==='node:readline')return{createInterface:()=>input};
 if(name==='@discordjs/voice')return voice;
 if(name==='opusscript')return O;
 if(name==='../music/dave_gate.cjs')return{proof:()=>({transportReady:true,protocol:1,sessionReady:encrypted,privacyPresent:encrypted,reinitializing:false,transitionExecuted:true,pendingTransitions:0}),encrypted:()=>encrypted,waitEncrypted:async()=>{if(scenario==='dave_timeout')throw Error('DAVE_WAIT_TIMEOUT');}};
 throw Error('Unexpected dependency: '+name);
},setTimeout:(fn,ms)=>{const t={fn,ms,unref(){}};timers.push(t);return t;},clearTimeout:()=>{},setImmediate:fn=>fn(),
process:{stdout:{write:s=>messages.push(JSON.parse(s))},stdin:{},on:()=>{},exit:()=>{},exitCode:0}};
vm.runInNewContext(fs.readFileSync(root+'/voice_probe/worker.cjs','utf8'),sandbox);
function send(m){input.emit('line',JSON.stringify(m));}
send({type:'init',guild:'100000000000000',channel:'200000000000000',user:'300000000000000',target:'400000000000000'});
connection.emit('stateChange',{}, {status:'ready'});
Promise.resolve().then(()=>Promise.resolve()).then(()=>{
 if(scenario==='other')speaking.emit('start','500000000000000');
 if(scenario==='dave_lost')encrypted=false;
 if(scenario==='decode'||scenario==='dave_lost'){
  const encoder=new O(48000,2,O.Application.AUDIO),pcm=Buffer.alloc(3840);
  for(let i=0;i<pcm.length;i+=2)pcm.writeInt16LE(Math.round(Math.sin(i/2*440*2*Math.PI/48000)*3000),i);
  const packet=encoder.encode(pcm,960);pcm.fill(0);encoder.delete();
  stream.emit('data',packet);wiped=packet.every(x=>x===0);
 }
 if(scenario==='stop'||scenario==='decode')send({type:'stop'});
 if(scenario==='timeout')timers.find(t=>t.ms===20000).fn();
 console.log(JSON.stringify({messages,subscribed,wiped,destroyed}));
});
'''


class Worker(unittest.TestCase):
    def run_scenario(self, name):
        if not shutil.which('node') or not (ROOT / 'voice_probe/node_modules/opusscript').exists():
            self.skipTest('Run npm ci --ignore-scripts in voice_probe first')
        result = subprocess.run(['node', '-e', HARNESS, str(ROOT), name],
                                capture_output=True, text=True, timeout=10, check=True)
        return json.loads(result.stdout)

    def test_real_opus_decode_counts_and_wipes_transient_buffers(self):
        result = self.run_scenario('decode')
        self.assertEqual(result['subscribed'], ['400000000000000'])
        summary = result['messages'][-1]
        self.assertEqual(summary['packets'], 1)
        self.assertGreater(summary['nonzeroSamples'], 0)
        self.assertTrue(result['wiped'])
        self.assertTrue(result['destroyed'])
        self.assertEqual(set(summary), {'type', 'code', 'packets', 'samples', 'nonzeroSamples', 'peak', 'daveProof'})

    def test_no_other_user_subscription_and_stop_on_other_speaker(self):
        result = self.run_scenario('other')
        self.assertEqual(result['subscribed'], ['400000000000000'])
        self.assertEqual(result['messages'][-1]['code'], 'OTHER_SPEAKER')
        self.assertEqual(result['messages'][-1]['packets'], 0)

    def test_dave_loss_fails_closed(self):
        result = self.run_scenario('dave_lost')
        self.assertEqual(result['messages'][-1]['code'], 'DAVE_NOT_READY')
        self.assertEqual(result['messages'][-1]['packets'], 0)

    def test_dave_wait_timeout_reports_fixed_proof_without_audio(self):
        result = self.run_scenario('dave_timeout')
        self.assertEqual(result['messages'][-1]['code'], 'DAVE_TIMEOUT')
        self.assertEqual(result['messages'][-1]['packets'], 0)
        self.assertTrue(result['messages'][-1]['daveProof']['transportReady'])

    def test_manual_stop_cleans_stream(self):
        result = self.run_scenario('stop')
        self.assertEqual(result['messages'][-1]['code'], 'STOPPED')
        self.assertTrue(result['destroyed'])

    def test_twenty_second_limit_cleans_stream(self):
        result = self.run_scenario('timeout')
        self.assertEqual(result['messages'][-1]['code'], 'TIME_LIMIT')
        self.assertTrue(result['destroyed'])
