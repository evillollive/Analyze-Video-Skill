# Analysis lenses

A lens decides what the analysis looks for and how the document is organized. The pipeline, the frame budget and the document builder are the same for every lens. Use one lens per document unless the user asks for two.

## Choosing

Infer the lens from the request. Ask only when it is unclear and the choice would change the document.

| Lens | Choose it when the user wants | The document answers |
|---|---|---|
| General | "analyze this video", no stated purpose | What happens, and what is notable about it? |
| Filmmaker breakdown | to learn how it was made, to make something like it, a shot or style breakdown | How was this built, and what can I reuse? |
| Content summary | the gist, notes, key points, "what does it say", a summary for people who will not watch | What does it say and show? |
| Reference review | to study a competitor, a client reference, or an example to learn from | What is it trying to do, how well does it work, and what should we take from it? |

Name the lens in the document's `subtitle` (for example "Filmmaker breakdown"), so a reader knows what kind of document it is.

## Limits that apply to every lens

Say these plainly in the document where they matter. Do not write around them.

- **Frames are samples.** Tiles on a contact sheet are seconds apart. A cut, a move or a short shot between two tiles is invisible. Shot counts taken from tiles are a minimum.
- **Motion is inferred.** A push-in, pan or handheld feel can only be read from how framing changes across neighboring tiles. Say "appears to" when the evidence is two stills.
- **Sound was not heard.** Speech is known only through the transcript. Music and effects are known only when captions mark them, for example `[Music]`. Do not describe a score, a voice or a mix.
- **People are described, not identified.** Name someone only when the title, captions or on-screen text name them.

## General

The default, described in Step 6 of `SKILL.md`: time-based sections, what is visible, transcript evidence, what is significant. Use it when no other lens clearly fits.

## Filmmaker breakdown

For a reader who makes video and wants to see the construction.

**Organize by beats, not by equal time slices.** Find where the piece turns: the hook, the setup, each change of subject or mood, the payoff, the close. Give each beat a section, with its time range in the heading, and say how long it runs. End with two extra sections:

- **How it was built:** the techniques that recur across the piece.
- **What to borrow:** three to six concrete, reusable choices, each pointing at a frame.

**Look for:**

- **Structure:** what the first ten seconds do to hold a viewer; where the title lands; how the ending is signaled; how much time each beat gets.
- **Coverage:** the kinds of shot and how they alternate. Interview or presenter setups (one camera or more, framing side, eyeline, background), b-roll over speech, inserts and details, screen recordings, archival, animation.
- **Composition and camera:** shot size, headroom, depth, how wide or long the lens looks, focus, angle, and any movement that neighboring tiles support.
- **Light and color:** where the light comes from, contrast, palette, and any shift in look between beats.
- **Editing:** pace, counted as distinct shots visible per minute on the contact sheet and stated as a minimum. Patterns such as intercutting two threads, cutaways covering speech, or a montage. Visible transitions.
- **Graphics and text:** titles, lower thirds, captions, callouts, logo placement, type and color, and when each appears.
- **Voice:** who carries the story (interview, narrator, presenter, text on screen), taken from the transcript, and what is shown while they speak.

**Frames:** prefer one frame per distinct setup or shot type, every graphic treatment, and any change in look. Use `--tiles` to bring those in when the automatic picks miss them.

## Content summary

For a reader who wants what the video says without watching it.

**Open with a summary paragraph** of three to five sentences, before the first section. Then organize by topic, in the order the video takes them, with time ranges in the headings. End with:

- **Key points:** the handful of things worth remembering.
- **Open questions:** claims made without support, and anything the video leaves unclear. Leave this out if there are none.

**Look for:**

- The claims, steps, decisions and conclusions, each with a timestamp.
- Exact quotes for the lines that matter most. Follow the caption quoting rules in Step 6.
- Numbers, names, dates and titles, including those that only appear on screen.
- What is demonstrated rather than said: a product flow, a result on screen, a chart.

Describe visuals only where they carry information. Skip camera and editing.

**Frames:** slides, charts, demos, and any on-screen text a reader would want to see.

## Reference review

For a reader studying someone else's video to inform their own work.

**Organize in four parts:**

1. **What it is for:** the audience it addresses, what it wants them to think or do, and where the call to action appears.
2. **How it makes its case:** the message, the proof offered (people, results, demonstrations, numbers), and the order they arrive in.
3. **How the brand shows up:** when and how the name or logo appears, color and type, tone of voice, who is on screen.
4. **Assessment:** what works, what does not, and what to adapt, each tied to a moment and a frame.

**Look for:** the opening promise, the format and length chosen for the platform, pacing, how product or service is shown, and the last thing the viewer sees.

**Keep observation and judgment apart.** Parts 1 to 3 report what is on screen and in the transcript. Part 4 gives opinions and labels them as such. Do not guess at budget, crew, or what the makers intended beyond what the video shows.

**Frames:** the opening, each brand moment, each proof point, and the call to action.

## Two lenses at once

If the user asks for two, write the document in the lens that matters most to them and add one closing section from the other, for example a content summary with a short "How it was built" section.
