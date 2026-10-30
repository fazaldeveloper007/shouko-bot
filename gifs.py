"""
Kristy's GIF library. Every URL below was verified to return a real GIF (HTTP 200).
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


# Character GIFs: when someone asks for a specific character's gif
# ("send a neuvillette gif"), she pulls from these lists. Aliases cover
# common spellings/nicknames; matching is case-insensitive whole-word.
CHARACTER_GIFS = {
    "neuvillette": {
        "aliases": ["neuvillette", "neuvi", "neuvilette", "neuvillete"],
        "gifs": [
            "https://media.tenor.com/GasP8hnRRGAAAAAM/neuvillette-neuvi.gif",
            "https://media.tenor.com/ewVSHH9T-JYAAAAM/neuvillette-genshin.gif",
            "https://media.tenor.com/LnMY5x202u4AAAAM/neuvillette.gif",
            "https://media.tenor.com/wmdYNgWaob0AAAAM/neuvillette.gif",
            "https://media.tenor.com/jXSbfea8U0gAAAAM/neuvillette-genshin-impact.gif",
            "https://media.tenor.com/hlzTnCXThPAAAAAM/neuvillette-genshin-impact.gif",
            "https://media.tenor.com/UobmA8MKy9sAAAAM/neuvillette-neuvilette.gif",
            "https://media.tenor.com/QRj9tXh8E_0AAAAM/neuvillette-genshin-impact.gif",
            "https://media.tenor.com/o63cmVMjmf8AAAAM/neuvillette-neuvillette-genshin.gif",
            "https://media.tenor.com/XfXyNkKlCyUAAAAM/neuvillette-chief-justice.gif",
            "https://media.tenor.com/2MLdo22wNK8AAAAM/neuvillette-neuvi.gif",
            "https://media.tenor.com/WHan5J0Nhd4AAAAM/neuvillette-genshin-impact.gif",
        ],
    },
    "vodyanitsa": {
        "aliases": ["vodyanitsa", "vodya", "vodyanista"],
        "gifs": [
            "https://media.tenor.com/Gk0vwwP9GBEAAAAC/vodyanitsa-genshin.gif",
            "https://media.tenor.com/Gk0vwwP9GBEAAAAM/vodyanitsa-genshin.gif",
            "https://media.tenor.com/CHCe7gt5UbgAAAAM/mitya-vodyanitsa.gif",
            "https://media.tenor.com/85BRnAUeFw0AAAAM/vodyanitsa-mem.gif",
            "https://media.tenor.com/QhR1iJG0UgEAAAAM/vodyanitsa-head-tilt.gif",
            "https://media.tenor.com/eM-vXVy-k-UAAAAM/vodyanitsa-genshinimpact.gif",
            "https://media.tenor.com/_edyYnDIBF0AAAAM/vodyanitsa-wow.gif",
            "https://media.tenor.com/GdlBRe7x-MAAAAAM/vodyanitsa-genshin-impact.gif",
        ],
    },
    "lohen": {
        "aliases": ["lohen"],
        "gifs": [
            "https://media.tenor.com/v9wWjETnRx0AAAAC/genshin-impact-lohen.gif",
            "https://media.tenor.com/v9wWjETnRx0AAAAd/genshin-impact-lohen.gif",
            "https://media.tenor.com/v9wWjETnRx0AAAAM/genshin-impact-lohen.gif",
            "https://media.tenor.com/PIjvQ7qDfO8AAAAM/lohen-genshin-lohen.gif",
            "https://media.tenor.com/Sp56maIVxWwAAAAM/lohen-upset-lohen-down.gif",
            "https://media.tenor.com/FHNTX3K1-OEAAAAM/loop-lohen.gif",
        ],
    },
    "mitya": {
        "aliases": ["mitya"],
        "gifs": [
            "https://media.tenor.com/26PjfxCH4W0AAAAC/mitya-genshin-impact-genshin-impact-mitya.gif",
            "https://media.tenor.com/26PjfxCH4W0AAAAM/mitya-genshin-impact-genshin-impact-mitya.gif",
            "https://media.tenor.com/wK9mtvLnGpEAAAAM/mitya-genshin-impact.gif",
            "https://media.tenor.com/_42L9fjMY-8AAAAM/mitya-genshin.gif",
            "https://media.tenor.com/jUMwp4ZqJmIAAAAM/mitya-genshin-impact-genshin-impact-mitya.gif",
            "https://media.tenor.com/S_lY1dwkjowAAAAM/mitya-genshin-impact-genshin-impact-mitya.gif",
        ],
    },
    "zibai": {
        "aliases": ["zibai", "zi bai", "zybau", "white horse adeptus", "white horse"],
        "gifs": [
            "https://media.tenor.com/-g_rDJ4Eu0cAAAAM/zibai-zibai-genshin.gif",
            "https://media.tenor.com/Kb3A8kAH5UcAAAAM/zibai-zibai-genshin-impact.gif",
            "https://media.tenor.com/-kf6-PojorcAAAAM/genshin-impact-genshin.gif",
            "https://media.tenor.com/Tx8poY5aqksAAAAM/genshin-impact-genshin.gif",
            "https://media.tenor.com/6n4RXpHPAwUAAAAM/zybau-zibai.gif",
            "https://media.tenor.com/FeGKzImQsx8AAAAM/zibai-genshin-impact.gif",
            "https://media.tenor.com/TDDhqrtD5_sAAAAM/low-cortisol-zibai.gif",
            "https://media.tenor.com/HjsQ-c0q_2IAAAAM/zibai.gif",
        ],
    },
    "tsaritsa": {
        "aliases": ["tsaritsa", "the tsaritsa", "cryo archon"],
        "gifs": [
            "https://media.tenor.com/9J4GdsIn0qsAAAAM/tsaritsa-genshin-impact.gif",
            "https://media.tenor.com/bqeAMP7klZUAAAAM/genshin-impact-tsaritsa.gif",
            "https://media.tenor.com/UQvOQe01IgkAAAAM/the-tsaritsa-genshin-impact.gif",
            "https://media.tenor.com/5UTyuWspnccAAAAM/tsaritsa-genshin-impact.gif",
            "https://media.tenor.com/PyqbIX5_I00AAAAM/tsaritsa-tsaritsa-genshin-impact.gif",
            "https://media.tenor.com/IRzHqo-RzdAAAAAM/tsaritsa-genshin-impact.gif",
            "https://media.tenor.com/7m6GA6nBAZkAAAAM/tsaritsa-tsaritsa-genshin-impact.gif",
        ],
    },
    "ronova": {
        "aliases": ["ronova", "ruler of death"],
        "gifs": [
            "https://media.tenor.com/nqfXo4vd2kAAAAAC/ronova-genshin.gif",
            "https://media.tenor.com/nqfXo4vd2kAAAAAd/ronova-genshin.gif",
            "https://media.tenor.com/nqfXo4vd2kAAAAAM/ronova-genshin.gif",
            "https://media.tenor.com/xEK9wPLBtAcAAAAM/ronova-ronova-closeup.gif",
            "https://media.tenor.com/0RE8mQI9Zk0AAAAM/ronova-ruler-of-death.gif",
            "https://media.tenor.com/0YZjLOG9Uw8AAAAM/ronova-ruler-of-death.gif",
        ],
    },
    "pierro": {
        "aliases": ["pierro", "the jester", "jester"],
        "gifs": [
            "https://media.tenor.com/B6krPor0_isAAAAC/ronova-pierro.gif",
            "https://media.tenor.com/B6krPor0_isAAAAM/ronova-pierro.gif",
            "https://media.tenor.com/3QBXSQrIkqQAAAAM/pierro-genshin-meme.gif",
            "https://media.tenor.com/dnCN3b2uXRMAAAAM/pierro-genshin-impact.gif",
            "https://media.tenor.com/OgdFSE1d0akAAAAM/pierro-genshin-impact.gif",
            "https://media.tenor.com/hjwiijvPlQsAAAAM/pierro-jester-genshin.gif",
            "https://media.tenor.com/Mwl6VxBneOIAAAAM/pierro-genshin-impact.gif",
        ],
    },
    "dainsleif": {
        "aliases": ["dainsleif", "dain", "dainslief", "bough keeper"],
        "gifs": [
            "https://media.tenor.com/qYKJRk8wB_cAAAAC/dainsleif.gif",
            "https://media.tenor.com/qYKJRk8wB_cAAAAM/dainsleif.gif",
            "https://media.tenor.com/tmNCZwaWG7EAAAAM/dain-dainsleif.gif",
            "https://media.tenor.com/qj-odRviYa0AAAAM/dainsleif-genshin-impact.gif",
            "https://media.tenor.com/z8MHO46s4DEAAAAM/dainsleif-dainsleif-rerir.gif",
            "https://media.tenor.com/QOBwGkvJyS8AAAAM/dainsleif-genshin-impact.gif",
            "https://media.tenor.com/4iSAvval8FQAAAAM/dainsleif-kaveh.gif",
        ],
    },
    "odette": {
        "aliases": ["odette"],
        "gifs": [
            "https://media.tenor.com/cv1uAmhqJAgAAAAC/odette-odette-genshin.gif",
            "https://media.tenor.com/cv1uAmhqJAgAAAAM/odette-odette-genshin.gif",
            "https://media.tenor.com/MEpWtg_J5n8AAAAM/odette-odette-genshin-impact.gif",
            "https://media.tenor.com/p8HosUHS3mAAAAAM/odette-odette-genshin-impact.gif",
            "https://media.tenor.com/A5B1mOA3nywAAAAM/odette-odette-genshin.gif",
            "https://media.tenor.com/8izwgjHsJuAAAAAM/odette-spessiva-genshin-impact.gif",
        ],
    },
    "vesna": {
        "aliases": ["vesna"],
        "gifs": [
            "https://media.tenor.com/b2iYdyEl7IcAAAAC/vesna-genshin.gif",
            "https://media.tenor.com/b2iYdyEl7IcAAAAM/vesna-genshin.gif",
            "https://media.tenor.com/jbWhCz2KbVUAAAAM/vesna-amazed-shocked-angry.gif",
            "https://media.tenor.com/v6Xjskl_Wr8AAAAM/vesna-genshin-impact.gif",
            "https://media.tenor.com/36VFPzjcCHUAAAAM/vesna-shocked.gif",
            "https://media.tenor.com/9K1KPO4HAEQAAAAM/vesna-hi.gif",
        ],
    },
    "columbina": {
        "aliases": ["columbina", "damselette"],
        "gifs": [
            "https://media.tenor.com/C8pktlj70HMAAAAC/columbina-columbina-genshin.gif",
            "https://media.tenor.com/C8pktlj70HMAAAAd/columbina-columbina-genshin.gif",
            "https://media.tenor.com/C8pktlj70HMAAAAM/columbina-columbina-genshin.gif",
            "https://media.tenor.com/n7tMq8K4dXQAAAAM/columbina-sandrone.gif",
            "https://media.tenor.com/iYOypqhLfToAAAAM/columbina-genshin-impact-columbina.gif",
            "https://media.tenor.com/SI8prMumrJwAAAAM/columbina-sad-columbina.gif",
            "https://media.tenor.com/ya9w2l46i40AAAAM/genshin-columbina.gif",
            "https://media.tenor.com/fqN6zLZAYcYAAAAM/columbina-columbina-hyposelenia.gif",
        ],
    },
    "noelle": {
        "aliases": ["noelle"],
        "gifs": [
            "https://media.tenor.com/lhLY6bukItgAAAAC/noelle-genshin-impact.gif",
            "https://media.tenor.com/lhLY6bukItgAAAAM/noelle-genshin-impact.gif",
            "https://media.tenor.com/sCdOeCFDO7wAAAAM/noelle-genshin-genshin-impact.gif",
            "https://media.tenor.com/wKgVf83UlBoAAAAM/genshin-impact-noelle.gif",
            "https://media.tenor.com/lm-zHXJ7AL8AAAAM/noelle-genshin.gif",
            "https://media.tenor.com/WOWlzXlEh8QAAAAM/genshin-impact-noelle.gif",
            "https://media.tenor.com/mE1pOJuDAgYAAAAM/genshin-noelle.gif",
        ],
    },
    "flins": {
        "aliases": ["flins"],
        "gifs": [
            "https://media.tenor.com/_PLxkddta2MAAAAC/flins-genshin.gif",
            "https://media.tenor.com/_PLxkddta2MAAAAM/flins-genshin.gif",
            "https://media.tenor.com/q8Zy9iFE3mcAAAAM/flins-genshin.gif",
            "https://media.tenor.com/vU0S_OQcWjgAAAAM/flins-flins-genshin.gif",
            "https://media.tenor.com/vJetSafjHwgAAAAM/flins-genshin.gif",
            "https://media.tenor.com/dMc-zhoewUoAAAAM/flins-flins-genshin.gif",
            "https://media.tenor.com/GFd_1rVL1acAAAAM/flins-flins-genshin.gif",
        ],
    },
    "lauma": {
        "aliases": ["lauma"],
        "gifs": [
            "https://media.tenor.com/dTfz322mwBsAAAAC/lauma-genshin.gif",
            "https://media.tenor.com/dTfz322mwBsAAAAM/lauma-genshin.gif",
            "https://media.tenor.com/Mq9aCQiGSaQAAAAM/lauma-genshin.gif",
            "https://media.tenor.com/QpZHmORC_boAAAAM/lauma-genshin.gif",
            "https://media.tenor.com/bzcvcCAfS80AAAAM/nod-krai-ellie-loves-lauma.gif",
            "https://media.tenor.com/bQ5t5SNCBa8AAAAM/lauma-lauma-genshin.gif",
            "https://media.tenor.com/ZmJzCz4gtqAAAAAM/lauma-lauma-genshin.gif",
        ],
    },
    "ganyu": {
        "aliases": ["ganyu"],
        "gifs": [
            "https://media.tenor.com/7w7p34lFW2oAAAAM/ganyu-genshin-impact.gif",
            "https://media.tenor.com/gxJZ15y0a44AAAAM/ganyu-genshin.gif",
            "https://media.tenor.com/O19b_FO_OIQAAAAM/ganyuanhoa.gif",
            "https://media.tenor.com/IZGGMcuI2K4AAAAM/ganyu-genshin.gif",
            "https://media.tenor.com/C1AbcbS5dywAAAAM/yazyu-ganyu.gif",
            "https://media.tenor.com/8ERjVliRQEIAAAAM/ganyu-gachapon.gif",
            "https://media.tenor.com/p9JZiVfI4AQAAAAM/ganyu-de-nana-ganyu-genshin-impact.gif",
            "https://media.tenor.com/1MbVPEKBQsAAAAAM/ganyu-genshin.gif",
        ],
    },
}

