"""
Shouko's GIF library. Every URL below was verified to return a real GIF (HTTP 200).
Add or remove URLs freely - she picks one at random per category. Discord
auto-embeds direct GIF links, so no extra API key is needed.

Categories (triggered by keywords in bot.py -> GIF_TRIGGERS):
    hello, good_morning, good_night, hype, sad, laugh, luck
"""

GIFS = {
    "hello": [
        "https://media.tenor.com/3g3D1mECft0AAAAM/anime-hi.gif",
        "https://media.tenor.com/9JMmuyam6KcAAAAM/hello-anime.gif",
        "https://media.tenor.com/FBojqU_OBfIAAAAM/anime-hello.gif",
        "https://media.tenor.com/Gp2MsdoLIv4AAAAM/waving-gif.gif",
        "https://media.tenor.com/6K9ZBPOyQ8IAAAAM/anime-hi.gif",
        "https://media.tenor.com/-ZDjEu483FkAAAAM/neuv-neuvillette.gif",  # Neuvillette
        "https://media.tenor.com/Xil_wTnX57AAAAAM/scaramouche-wanderer-genshin-impact.gif",  # Wanderer / Scaramouche
        "https://media.tenor.com/HqIUHD0nvngAAAAM/ganyu.gif",  # Ganyu
        "https://media.tenor.com/L0DmTpOyXLUAAAAM/elysia-loves-skirk-skirk-idle-animations.gif",  # Skirk
        "https://media.tenor.com/9W0cdGlncQMAAAAM/il-dottore-dottore.gif",  # Il Dottore
        "https://media.tenor.com/WO18h5KYWE8AAAAM/nahida-happy.gif",  # Nahida
        "https://media.tenor.com/mmCgTIKi6OwAAAAM/kaedehara-kazuha-genshin-impact.gif",  # Kaedehara Kazuha
    ],
    "good_morning": [
        "https://media.tenor.com/0xZPQX-922MAAAAM/spike-gm-good-morning.gif",
        "https://media.tenor.com/6IicLfOaw1AAAAAM/tora-dora-good-morning.gif",
        "https://media.tenor.com/BeTFNE3oO8UAAAAM/gm.gif",
        "https://media.tenor.com/FJGye-lXhm4AAAAM/sleepy-just-woke-up.gif",
        "https://media.tenor.com/dRMyqMyhyiIAAAAM/neuvillette-good-morning.gif",  # Neuvillette
    ],
    "good_night": [
        "https://media.tenor.com/7CptjqNfQKwAAAAM/gn-good-night.gif",
        "https://media.tenor.com/J0cm0t5ZJQwAAAAM/ai-good-night-ai-hoshino-good-night.gif",
        "https://media.tenor.com/JC3pEMByXp8AAAAM/go-to-sleep-anime.gif",
        "https://media.tenor.com/EiHHJZv_FfgAAAAM/gn-owo.gif",
        "https://media.tenor.com/vlMHGXzYhQIAAAAM/ganyu-ganyu-tired.gif",  # Ganyu
        "https://media.tenor.com/TYaPj0kxAP0AAAAM/ganyu-ganyu-sleep.gif",  # Ganyu
    ],
    "hype": [
        "https://media.tenor.com/44ww3AqjYlgAAAAM/anime-excited.gif",
        "https://media.tenor.com/HcH6HU65aUMAAAAM/happy-excited.gif",
        "https://media.tenor.com/fzi3EhR9fj4AAAAM/hilicurls-hilicurls-dance.gif",
        "https://media.tenor.com/onpfBSIeKBUAAAAM/venti-dance.gif",
        "https://media.tenor.com/rTBW4f6YhUkAAAAM/furina-rat-dance.gif",
        "https://media.tenor.com/amVbufvJg5sAAAAM/nahida-dance.gif",
        "https://media.tenor.com/iUXRF_vSVmgAAAAM/neuvillette-clapping.gif",  # Neuvillette
        "https://media.tenor.com/Axh70e7PO7UAAAAM/wanderer-genshin.gif",  # Wanderer / Scaramouche
        "https://media.tenor.com/YJO6W8N_ydYAAAAM/furina-excited.gif",  # Furina
        "https://media.tenor.com/ZKSRRgUN_4MAAAAM/elysia-loves-skirk-skirk-ultimate-burst.gif",  # Skirk
        "https://media.tenor.com/_ljSKbqhdIwAAAAM/skirk-cryo-gang.gif",  # Skirk
        "https://media.tenor.com/G7WgnN5o9coAAAAM/dottore-il-dottore.gif",  # Il Dottore
        "https://media.tenor.com/Y2bGyS7McVIAAAAM/i-will-have-order.gif",  # Zhongli
    ],
    "sad": [
        "https://media.tenor.com/8FVJdijHFRkAAAAM/sad-anime.gif",
        "https://media.tenor.com/4GD6-EEc1toAAAAM/anime-cry-missing.gif",
        "https://media.tenor.com/Dh7AUXzPa1gAAAAM/sading.gif",
        "https://media.tenor.com/af8RmPBBt6MAAAAM/qiqi-dies.gif",
        "https://media.tenor.com/vRL2z5-nwa8AAAAM/furina-sad.gif",  # Furina
    ],
    "laugh": [
        "https://media.tenor.com/GDdH2RYzFx8AAAAM/lol-anime-lol.gif",
        "https://media.tenor.com/6M8Kle5aqBMAAAAM/lol-lmao.gif",
        "https://media.tenor.com/bUwoEuyVZHcAAAAM/lmao-lol.gif",
        "https://media.tenor.com/S_V4Q8NA9QQAAAAM/tsugumomo-laugh.gif",
        "https://media.tenor.com/O_NhRPrNYV4AAAAM/evil-laugh-ohoho.gif",
        "https://media.tenor.com/LRqstN7vjJIAAAAM/dottore-dottore-genshin.gif",  # Il Dottore
        "https://media.tenor.com/bYjO5iNbgG0AAAAM/nahida-giggle.gif",  # Nahida
        "https://media.tenor.com/irT_rWtnYl8AAAAM/raiden-shogun-dance.gif",  # Raiden Shogun
    ],
    "luck": [
        "https://media.tenor.com/-2fB7XE510QAAAAM/gacha-rolls.gif",
        "https://media.tenor.com/Y-WsU9ZRAeUAAAAM/intertwined-fate-pulls.gif",
        "https://media.tenor.com/rOuL0G1uRpMAAAAM/genshin-impact-pull.gif",
        "https://media.tenor.com/bAZCBwnnkRYAAAAM/genshin-capturing-radiance.gif",
        "https://media.tenor.com/ZCJBKHmy6IcAAAAM/gacha.gif",
        "https://media.tenor.com/ZSPFbDGQOrsAAAAM/best-of-luck-best-wishes.gif",
        "https://media.tenor.com/Bq22J3FTz-cAAAAM/scaramouche-wanderer.gif",  # Wanderer / Scaramouche
        "https://media.tenor.com/x8BnLOgYuygAAAAM/furina-approves.gif",  # Furina
    ],
}
