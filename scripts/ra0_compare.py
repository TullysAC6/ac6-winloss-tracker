"""Compare frozen foreground v1 against adopted RA-0 polls (no product writes).

Usage: python -B scripts/ra0_compare.py POLLS_ROOT EVIDENCE_ROOT OUTPUT_ROOT TAG...
Event replay invokes unchanged production state/gate methods with cached capture
and gameplay-activity inputs. This is a controlled classifier intervention, not
a native capture/persistence test or a claim of integrated runtime behavior.
The baseline control must reproduce EVERY state snapshot/detection/gate call.
"""
import hashlib,json,sys,time
from pathlib import Path
from collections import Counter
import numpy as np
from ra0_foreground import Foreground,PARAMETERS
REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
import result_detector as rd
from result_gate import ResultGate
SOURCES={'vP':'vP-M8cPgMtE','LT25':'LT25joZOjhU','fAph':'fAphPer-yas','IXV8':'IXV8Y_t6U34','yB':'yB_Hcy45v5c','AYng':'AYngtoqhwKs'}

def state_replay(polls,classes,processing_ms):
    state=rd.ResultStateMachine();gate=ResultGate();out=[]
    for p,cls in zip(polls,classes):
        now=100+p['at_ms']/1000.;process=now+processing_ms/1000.
        if p['capture']['discontinuity'] or p['capture']['identity_changed']:
            raise ValueError('this controlled replay supports continuous adopted video polls only')
        activity=p['gameplay_activity'] if cls in (rd.CLEAR,rd.NON_CLEAR) else False
        result=state.observe(cls,rd.CONFIRM_HITS,rd.CLEAR_HITS_REQUIRED,rd.COOLDOWN_SECONDS,now=now,
                             gameplay_activity=activity)
        calls=[]
        if result in ('win','loss'):
            accepted=gate.try_accept(5.,now=process)
            if accepted:state.external_mutation(now=process)
            calls=[dict(result=result,source='auto',accepted=accepted)]
        # Detector health/diagnostics are not replayed by this intervention.
        # Do not copy baseline-derived fields into a new result trace.
        row={k:p[k] for k in ('id','at_ms','poll_t','source_frame','motion_score','capture')}
        out.append(dict(row,frame_class=cls,state=state.snapshot(),detections=[result] if result else [],gate_calls=calls,
                        gameplay_activity=activity,cached_gameplay_activity=p['gameplay_activity']))
    return out

def main():
    polls_root,evidence,outroot=map(Path,sys.argv[1:4]);outroot.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(evidence));from ra0_evaluate import evaluate
    model=Foreground(REPO/'detector_templates.json')
    for tag in sys.argv[4:]:
        started=time.monotonic();truth_path=REPO/'docs/issue73-ra0/ground-truth'/f'{SOURCES[tag]}.json'
        truth=json.loads(truth_path.read_text(encoding='utf-8'))
        result={'source':tag,'parameters':PARAMETERS,'split_sha256':hashlib.sha256((REPO/'docs/issue73-ra0/foreground-split.json').read_bytes()).hexdigest(),
                'truth_sha256':hashlib.sha256(truth_path.read_bytes()).hexdigest(),'phases':[]}
        for i in range(4):
            root=polls_root/tag;meta=json.loads((root/f'phase{i}.json').read_text());arr=np.load(root/f'phase{i}.npy',mmap_mode='r')
            raw=[json.loads(l) for l in (root/f'replay{i}.jsonl').read_text().splitlines()];header,polls=raw[0],raw[1:]
            assert arr.shape==(len(polls),meta['roi'][3],meta['roi'][2],3)
            control=state_replay(polls,[p['frame_class'] for p in polls],header['processing_ms'])
            assert all(all(a[k]==b[k] for k in ('state','detections','gate_calls')) for a,b in zip(control,polls)),(tag,i,'baseline control mismatch')
            classes=[];frame=Counter();interesting=[]
            for k,(p,pixels) in enumerate(zip(polls,arr)):
                if time.monotonic()-started>1800:raise TimeoutError('owned source comparison exceeded 30 minutes')
                cls,debug=model.classify(pixels)
                if cls is None:cls=rd.NON_CLEAR if p['frame_class'] in (rd.FINAL_WIN,rd.FINAL_LOSS) else p['frame_class']
                classes.append(cls)
                event=next((e for e in truth['events'] if e['banner_first_visible_s']<=p['poll_t']<=e['banner_last_visible_s'] and e['result']!='DRAW'),None)
                if event:
                    frame['banner_interval_polls']+=1
                    frame['current_matching']+=p['frame_class']=='FINAL_'+event['result']
                    frame['poc_matching']+=cls=='FINAL_'+event['result']
                    frame['current_wrong']+=p['frame_class'] in (rd.FINAL_WIN,rd.FINAL_LOSS) and p['frame_class']!='FINAL_'+event['result']
                    frame['poc_wrong']+=cls in (rd.FINAL_WIN,rd.FINAL_LOSS) and cls!='FINAL_'+event['result']
                if cls!=p['frame_class']:
                    interesting.append(dict(poll=k,t=p['poll_t'],current=p['frame_class'],poc=cls,event=event['event_id'] if event else None,debug=debug))
                if not any(e['banner_first_visible_s']-3<=p['poll_t']<=e['banner_last_visible_s']+10 for e in truth['events']):
                    frame['negative_window_polls']+=1
                    frame['current_final_wl_negative']+=p['frame_class'] in (rd.FINAL_WIN,rd.FINAL_LOSS)
                    frame['poc_final_wl_negative']+=cls in (rd.FINAL_WIN,rd.FINAL_LOSS)
            changed=state_replay(polls,classes,header['processing_ms'])
            # Wrong-class armed first hits, including those hidden by the deliberately generous scoring windows.
            near={'current':[],'poc':[]}
            for name,rows in [('current',polls),('poc',changed)]:
                for p in rows:
                    if p['state']['candidate_hits']!=1:continue
                    event=next((e for e in truth['events'] if e['banner_first_visible_s']-1<=p['poll_t']<=e['banner_last_visible_s']+1),None)
                    if event is None or p['state']['candidate']!=event['result'].lower():
                        near[name].append(dict(t=p['poll_t'],candidate=p['state']['candidate']))
            phase={'phase':i,'baseline_control_all_states_identical':True,'current':evaluate(truth,header,polls),'poc':evaluate(truth,header,changed),'frame_signal_counts':dict(frame),'near_fp':near,'changed_frames':interesting}
            result['phases'].append(phase)
            dest=outroot/tag;dest.mkdir(exist_ok=True)
            with (dest/f'poc-replay{i}.jsonl').open('w',encoding='utf-8') as f:
                trace_header={k:v for k,v in header.items() if k!='flush_reasons'}
                trace_header.update(experiment='controlled_state_intervention',prototype_version=PARAMETERS['version'])
                f.write(json.dumps(trace_header)+'\n')
                for p in changed:f.write(json.dumps(p)+'\n')
            print(tag,i,'control PASS',phase['current']['outcomes'],'->',phase['poc']['outcomes'],'FP',len(phase['poc']['false_positives']),'changed',len(interesting),flush=True)
            del arr
        result['seconds']=time.monotonic()-started
        (outroot/tag/'comparison.json').write_text(json.dumps(result,indent=1),encoding='utf-8')
        print(tag,'COMPLETE',round(result['seconds'],1),'seconds',flush=True)
if __name__=='__main__':main()
