"""Additive slash registration with locally recorded ownership, never bulk sync."""
import json
import pathlib
import discord

async def register_commands(client, tree, guild_id=None, ledger_path=None):
    path=pathlib.Path(ledger_path or '.music-command-ownership.json')
    application=client.application_id
    scope=str(guild_id or 'global')
    data=json.loads(path.read_text()) if path.exists() else {'application_id':application,'scopes':{}}
    if data.get('application_id')!=application:raise RuntimeError('Music command ledger belongs to another application')
    owned=data['scopes'].setdefault(scope,{})
    http=client.http
    async def read():
        return await http.get_guild_commands(application,guild_id) if guild_id else await http.get_global_commands(application)
    commands=list(tree.get_commands())
    current=await read()
    for c in commands:
        found=[v for v in current if v.get('name')==c.name]
        if found and (len(found)!=1 or owned.get(c.name)!=str(found[0]['id'])):
            raise RuntimeError('Music command name collision; unrelated commands left unchanged')
    if guild_id:tree.copy_global_to(guild=discord.Object(id=guild_id))
    def save():
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,sort_keys=True));temp.replace(path)
    save()
    for c in commands:
        current=await read();found=[v for v in current if v.get('name')==c.name]
        payload=c.to_dict(tree)
        if found:
            if len(found)!=1 or owned.get(c.name)!=str(found[0]['id']):raise RuntimeError('Command changed during registration')
            result=(await http.edit_guild_command(application,guild_id,int(found[0]['id']),payload=payload) if guild_id else await http.edit_global_command(application,int(found[0]['id']),payload=payload))
        else:
            result=(await http.upsert_guild_command(application,guild_id,payload=payload) if guild_id else await http.upsert_global_command(application,payload=payload))
        owned[c.name]=str(result['id']);save()
    current={str(v['id']):v.get('name') for v in await read()}
    if any(current.get(cid)!=name for name,cid in owned.items()):raise RuntimeError('Music registration confirmation failed; keep ownership ledger')
