"""English Twitter/X drafts. This script never posts anything."""
import html,json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parent
URL='http://vlmeval.byted.org/reports/view/rzAw-KSgopFj/'

def weighted_length(text):
    # These drafts use ASCII text. Twitter wraps each URL as a23-character t.co link.
    return len(re.sub(r'https?://\S+','x'*23,text))

def main():
    drafts=[
      {'id':'curiosity','label':'Recommended opening','text':f'''I built a maze where every wall is a mirror.

A corridor can be an illusion. So can the goal.

One run:499 collision-free planning decisions. Still no exit.

Try it yourself, then inspect what the models did:
{URL}'''.replace('run:499','run: 499')},
      {'id':'exploration','label':'The surprising failure','text':f'''GPT-6 Astra avoided wall collisions for499 planning decisions in a mirror maze.

It still failed to finish.

The replay shows long stretches of turning and revisiting. Safe actions did not add up to effective exploration.

{URL}'''.replace('for499','for 499')},
      {'id':'metric','label':'Evaluation angle','text':f'''How much work can hide inside one agent "step"?

In our mirror-maze protocol, a blind100,000-action sequence counts as one planning decision.

That stress test changed how I read the results. Actions, coverage and collisions belong beside pass rates.

{URL}'''.replace('blind100','blind 100')}
    ]
    thread=[
      f'''1/ I built a maze where every wall is a mirror.

You can see a corridor that does not exist, or a goal you cannot actually reach.

Models get first-person images and three actions:left, right, forward. Can they keep track of a real route?

{URL}'''.replace('actions:left','actions: left'),
      '''2/ One plan may contain multiple actions. The next turn receives per-action images; models never see the spectator map.

The first study has40 runs:five models, four fixed easy mazes, native clients vs a public-text-history API baseline.'''.replace('has40','has 40').replace('runs:five','runs: five'),
      '''3/ In that small study, GPT-6.1 Sol solved4/4 with Codex and1/4 in the API baseline.

Session-state handling differs, but so do prompts and defaults. This is a configuration comparison, not an isolated causal result.'''.replace('solved4','solved 4').replace('and1','and 1'),
      '''4/ On four larger mazes, GPT-6 Astra + Codex solved3/4 with a500-decision cap.

The failed run had zero collisions through decision499, including a41-decision plateau at38 visited cells. Its final89-action batch caused all38 collisions.'''.replace('solved3','solved 3').replace('a500','a 500').replace('decision499','decision 499').replace('a41','a 41').replace('at38','at 38').replace('final89','final 89').replace('all38','all 38'),
      '''5/ I also tested a blind, code-generated one-plan policy. With a100,000-action limit it solved94.65% of easy-map trials, at a mean27,704 actions and7,066 collisions.

Not a model score or compute-matched baseline:a stress test of what the decision metric rewards.'''.replace('a100','a 100').replace('solved94','solved 94').replace('mean27','mean 27').replace('and7','and 7').replace('baseline:a','baseline: a'),
      f'''6/ The project page has a playable maze, all44 model-run replays, English per-case videos, the protocol, and the offline stress-test method.

Small fixed maps, no mirror-vs-matte control yet. Plenty left to test.

{URL}'''.replace('all44','all 44')
    ]
    for d in drafts:d['weighted_characters']=weighted_length(d['text']);assert d['weighted_characters']<=280,(d['id'],d['weighted_characters'])
    for i,text in enumerate(thread,1):assert weighted_length(text)<=280,(i,weighted_length(text))
    out=ROOT/'twitter';out.mkdir(exist_ok=True)
    data={'drafts':drafts,'thread':[{'post':i,'text':t,'weighted_characters':weighted_length(t)}for i,t in enumerate(thread,1)],'post_status':'draft_only_not_posted','recommended_media':'../videos/mirror-maze-teaser-48s.mp4','video_alt_text':'Five model columns show recorded first-person views and spectator-only maps in square maze MIRROR-043. GPT-6.1 Sol with Codex reaches the goal at planning decision 49. Counters distinguish plans, motor actions and collisions. Playback is aligned by planning decision and does not measure inference latency.','link':URL}
    (out/'drafts.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    cards=''.join('<section class="draft"><p class="tag">'+html.escape(d['label'])+'</p><textarea readonly>'+html.escape(d['text'])+'</textarea><div><button>Copy draft</button><span>'+str(d['weighted_characters'])+' /280 characters'.replace('/280','/ 280')+'</span></div></section>'for d in drafts)
    posts=''.join('<section class="draft"><p class="tag">Thread post '+str(i)+'</p><textarea readonly>'+html.escape(t)+'</textarea><div><button>Copy post</button><span>'+str(weighted_length(t))+' /280 characters'.replace('/280','/ 280')+'</span></div></section>'for i,t in enumerate(thread,1))
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mirror Maze - Twitter drafts</title><link rel="stylesheet" href="../style.css"><style>main{max-width:850px}.intro{padding:60px 0 25px}h1{font-size:45px;margin-bottom:20px}.draft{padding:22px 0;border-top:1px solid #e3e9eb}.tag{font:12px var(--ui);color:#81919a;margin-bottom:14px}textarea{width:100%;height:200px;border:1px solid #e0e7eb;border-radius:4px;background:#fafbfc;color:#526d7b;font:14px/1.8 var(--ui);padding:16px;resize:vertical}.draft>div{display:flex;align-items:center;gap:20px;margin-top:10px;font:11px var(--ui);color:#8a9ba5}.draft button{font:12px var(--ui);padding:7px 13px;background:white;border:1px solid #d7e1e6;border-radius:3px;color:#5b7686}.media{margin:25px 0 35px}.media video{width:100%}h2{margin-top:45px}.intro p{font-size:17px}.notes{font:12px/1.8 var(--ui);color:#8999a2}footer{margin-top:40px}</style><header class="masthead"><a class="wordmark" href="../">Mirror Maze</a><nav><a href="../">Project page</a></nav></header><main><section class="intro"><h1>Twitter drafts</h1><p>Lead with a visual question, then let the recorded behavior carry the story. The recommended opening pairs with the48-second clip. The six-post thread adds the protocol and the metric stress test.</p><p class="notes">These are drafts for selection. Nothing has been posted. All entries fit280 characters after URL wrapping; no public-code release is promised.</p></section><div class="media"><video controls preload="metadata" src="../videos/mirror-maze-teaser-48s.mp4" poster="../assets/poster.png"></video><p><a href="../videos/mirror-maze-teaser-48s.mp4" download>Download the English48-second video</a> · <a href="../assets/social-card.png" download>Download the social card</a></p></div><h2>Choose an opening</h2>'''+cards+'''<h2>A thread with the evidence</h2>'''+posts+'''<h2>Video alt text</h2><p class="notes">'''+html.escape(data['video_alt_text'])+'''</p><p><a href="drafts.json" download>Download all drafts as JSON</a></p><footer>Prepared for Kenny · Mirror Maze</footer></main><script>document.querySelectorAll('.draft button').forEach(b=>b.onclick=async()=>{const t=b.closest('section').querySelector('textarea');try{if(navigator.clipboard&&isSecureContext)await navigator.clipboard.writeText(t.value);else{t.focus();t.select();if(!document.execCommand('copy'))throw Error();}b.textContent='Copied';}catch(e){t.focus();t.select();b.textContent='Selected - press Ctrl/Cmd+C';}});</script></html>'''
    page=page.replace('the48-second','the 48-second').replace('fit280','fit 280').replace('English48-second','English 48-second')
    (out/'index.html').write_text(page)
    print(json.dumps({'opening_counts':[d['weighted_characters']for d in drafts],'thread_counts':[weighted_length(t)for t in thread],'posted':False}))

if __name__=='__main__':main()
