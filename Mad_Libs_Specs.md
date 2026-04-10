#### **Mad Libs: Collaborative Story (candidate for Day 2)**

The audience collectively builds an absurd PyCon-themed story over the course of the entire session, one word at a time. Before the session, we prepare a story template with 12-15 blanks. After each lightning talk, during the transition, a slide appears asking the audience to pick a word. At the end of the session, the host reads the complete story out loud in the most dramatic voice possible.

**How word collection works (tldr; pick from a pre-determined list):**

We do NOT use open text input (too slow to moderate in a big room, risk of rude answers). Instead, after each talk, the audience sees a screen on the web tool showing 5 or 6 pre-selected words for the current blank. The words are curated in advance to all be funny but in different ways. The audience doesn't know which part of the story they're filling, they just see the category (e.g. "pick a NOUN") and the options.

**The flow, step by step:**

1. A talk finishes. The host says: "The story needs a NOUN, vote now!"
2. On their phones, the audience sees 5 or 6 word options (e.g. "kangaroo", "spreadsheet", "lasagna", "saxophone", "grandma", "blockchain")
3. Everyone taps their pick. A 30-second countdown runs.
4. While the countdown runs, the audience sees a **live-updating results page**: a bar chart / tally showing the vote distribution in real time. This is the engagement hook: people watch the bars shift, cheer for their word, try to rally others.
5. When the timer hits zero, the winning word is revealed on the big screen.

**The twist: variable selection strategies:**

The audience assumes the most popular word always wins. But we secretly rotate the selection strategy each round, which means voting patterns don't guarantee outcomes. This keeps things unpredictable and makes the final story wilder than a pure popularity contest would.

Strategies to rotate through:

- **Most popular**: the word with the most votes (what everyone expects)
- **Least popular**: the underdog word nobody voted for, revealed with dramatic flair ("only THREE of you voted for this... and yet...")
- **Random pick**: weighted or unweighted random draw from all submissions
- **Second place**: the runner-up, not the winner
- **Host's choice**: the host overrides and picks whatever's funniest for the story so far (the "wild card" round)
- **Inverse momentum**: if one word dominated early, pick the one that surged latest

*The audience doesn't know which strategy is being used each round. The host can announce it after the reveal for added comedy ("this round, we went with... the LEAST popular word!") or keep it a mystery until the end.*

**Why this design works:**

- **No moderation needed**: all words are pre-curated, so nothing inappropriate can appear
- **Scales to any crowd**: 50 or 500 people, same experience
- **Everyone participates**: tapping a button is lower friction than typing a word
- **The live stats are the engagement hook**: watching bars shift in real-time is inherently compelling (think election night energy)
- **The variable strategy is the twist**: it means the audience can't "game" the result, and the story stays surprising even if one word dominates
- **It's cumulative**: unlike bingo where engagement can drift, people stay curious about how the story turns out
- **Natural pacing rhythm**: talk → vote → reveal → talk → vote → reveal
- **Built-in climax**: the full story read aloud at the end is the session's big finale
- **Zero effort from speakers**: the game is entirely between the host and the audience

**Extra chaos layer: misleading questions:**

To make things even more unhinged, the audience doesn't see "pick a NOUN." Instead, they see what looks like a genuine conference opinion poll, a completely unrelated question whose answers secretly double as story words. The audience thinks they're voting on a real debate. They have no idea their answer is about to be dropped into a fantasy quest.

The story template is a medieval/epic quest (hidden from the audience). The questions are conference polls (shown to the audience). The collision is where the comedy lives.

| **Round** | **What the audience sees on their phone**   | **What it secretly fills in the story** | **The options**                                              |
| --------- | ------------------------------------------- | --------------------------------------- | ------------------------------------------------------------ |
| 1         | "What's the best Python web framework?"     | The hero's MOUNT                        | Flask, Django, FastAPI, Bottle, Tornado                      |
| 2         | "Worst thing to find in a code review?"     | The MONSTER in the cave                 | Spaghetti code, Global variables, Magic numbers, A TODO from 2019 |
| 3         | "Most important soft skill for developers?" | A MAGIC SPELL                           | Communication, Empathy, Patience, Collaboration              |
| 4         | "Best conference fuel?"                     | The hero's WEAPON                       | Pretzels, Coffee, Club-Mate, Gummy bears                     |
| 5         | "What do you mass-delete on Fridays?"       | The TREASURE to protect                 | Old branches, Slack notifications, Docker images, Jira tickets |
| 6         | "Tabs or spaces?"                           | The hero's BATTLE CRY                   | Tabs!, Spaces!, Whatever the linter says!, I use both and I'm not sorry! |
| 7         | "What's your deployment ritual?"            | How the hero DEFEATS the final boss     | Pray and push, Run the tests twice, Blame the intern, Roll back immediately |



**How the story reveal might sound (read dramatically by the host):**

"Our hero mounted their trusty **Django** and rode into the Pythonic Wastes. Deep in the cave, they found a terrifying **TODO from 2019** blocking the path. The hero raised their staff and cast a devastating **Empathy** spell, but the beast barely flinched. Drawing their mighty **Club-Mate**, they charged forward, screaming '**Whatever the linter says!**' at the top of their lungs. After an epic struggle, they seized the legendary hoard of **Slack notifications** and, with one final move, **blamed the intern**. Peace was restored to the land of PyCon. The end."

This works because:

- The audience is MORE engaged than with a plain "pick a NOUN"; they're debating a real opinion ("Django is obviously better!")
- The reveal is a double punchline: first the word out of context, then the realisation that the whole session was building a ridiculous story without anyone noticing
- The options are already curated (no moderation needed) and every single answer is inherently funny in the wrong context
- It adds a "meta-game" layer where, on Day 2, the audience might start to suspect the questions aren't what they seem

**This can be mixed with the standard approach:** Some rounds use the misleading questions, some use the straightforward "pick a NOUN" format. Keeps the audience off-balance.



------



**Full example (standard format) story template:**

"Once upon a time at PyCon, a developer named **(1: NAME)** tried to deploy their **(2: ADJECTIVE)** app to **(3: PLACE)**. But the CI pipeline started **(4: VERB ending in -ing)** and the only fix was to **(5: VERB)** the entire **(6: NOUN)**. They called their colleague who shouted: '**(7: EXCLAMATION)**!' So they rewrote everything in **(8: PROGRAMMING LANGUAGE)**, which somehow made the **(9: NOUN)** even more **(10: ADJECTIVE)**. In the end, the app worked perfectly, but only on **(11: DAY OF THE WEEK)**s, and only if you whispered '**(12: SILLY WORD)**' to the terminal first."

**Example of how it might play out:**

"Once upon a time at PyCon, a developer named **Günther** *(most popular)* tried to deploy their **spicy** *(least popular, only 4 votes!)* app to **the Moon** *(random pick)*. But the CI pipeline started **yodelling** *(most popular)* and the only fix was to **tickle** *(host's choice)* the entire **kangaroo** *(second place)*. They called their colleague who shouted: '**WAAALUIGI!**' *(most popular)* So they rewrote everything in **COBOL** *(least popular)*, which somehow made the **saxophone** *(random)* even more **magnificent** *(most popular)*. In the end, the app worked perfectly, but only on **Thursday**s *(host's choice)*, and only if you whispered '**bloop**' *(least popular)* to the terminal first."

**Preparation needed:**

- Pre-write 2 or 3 story templates (one per session + backup)
- For each blank, curate 5 or 6 word options that are all funny but tonally different
- Decide the strategy rotation order in advance (can be written on a card for the host)

**Web tool requirements:**

- **Audience view (phone):** Shows either a "pick a NOUN" label or the misleading poll question (e.g. "What's the best Python web framework?"), 5 or 6 word/answer buttons, a countdown timer. After voting, switches to a live-updating bar chart showing vote distribution. Self-refreshes.
- **Big screen view (projector):** Shows the same live bar chart at large scale, then the winning word reveal with the strategy used. For the misleading questions format, the big screen should show the poll question (not the story context) during voting; the story context is only revealed at the end.
- **Host dashboard (backstage):** Shows real-time results, the current selection strategy, a manual override button, and the mapping between the poll question and the story blank.