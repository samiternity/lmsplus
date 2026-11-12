const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('app/static/js/lms_lecture_notes_new.js','utf8');
const start = source.indexOf('    // Each request keeps its own target.');
const end = source.indexOf('    // Notification helper', start);

function setup() {
  const submissions = [], nodes = {}, messages = [];
  const resetTimers = new Map();
  let nextTimer = 0;
  class Element {
    constructor(tag='div') {this.tagName=tag; this.children=[]; this.style={}; this.attributes={}; this.contentWindow={};}
    appendChild(node) {node.parent=this; this.children.push(node); return node;}
    append(...children) {children.forEach(child=>this.appendChild(child));}
    replaceChildren(...children) {this.children=[];this.append(...children);}
    replaceWith(...children) {
      const index=this.parent.children.indexOf(this);
      this.parent.children.splice(index,1,...children); children.forEach(child=>child.parent=this.parent);
    }
    setAttribute(name,value) {this.attributes[name]=value;}
    querySelectorAll(selector) {
      return this.children.flatMap(child=>[...(selector==='button' ? child.tagName==='button' : child.className===selector.slice(1)) ? [child] : [], ...child.querySelectorAll(selector)]);
    }
    querySelector(selector) {return this.querySelectorAll(selector)[0];}
    focus() {this.focused=true;}
    remove() {}
    click() {if(!this.disabled) this.onclick?.();}
    submit() {submissions.push(this);}
  }
  for (const id of ['lectureDownloadPanel','lectureDownloadStatus','lectureDownloadParts','lectureDownloadHeading','lectureDownloadReview','lectureDownloadClose','lectureDownloadFeedback','lectureDownloadRecovery','lectureDownloadToolbar','lectureDownloadButtonLabel','lectureDownloadAnnouncement']) nodes[id]=new Element();
  nodes.lectureDownloadPanel.hidden=true;
  nodes.lectureNotesScript={dataset:{downloadOrigin:'https://downloads.example.test'}};
  const instruction = (count,start=0) => ({count,start,end:start+count,weeks:[1,2], filename:'Lectures.zip',
    method:'POST',download_url:'https://downloads.example.test/zip',expires_at:Date.now()/1000+900,
    form_fields:{token:'encrypted'}, files:Array.from({length:count},(_,i)=>({name:`File${i}.pdf`,index:start+i,
      method:'POST',download_url:'https://downloads.example.test/download',expires_at:Date.now()/1000+900,form_fields:{token:'individual'}}))});
  const context = vm.createContext({URL,URLSearchParams,console,Date,AbortController,
    clearTimeout:id=>resetTimers.delete(id),setTimeout:(fn,delay)=>{
      if (delay===3000) {const id=++nextTimer;resetTimers.set(id,fn);return id;}
      fn();return -1;
    },
    downloadAllBtn:{disabled:false,focus(){}},courseSelect:{selectedIndex:0,options:[{text:'Course'}]},
    document:{addEventListener(){},getElementById:id=>nodes[id],createElement:tag=>new Element(tag),createTextNode:text=>Object.assign(new Element(),{textContent:text}),body:new Element('body')},
    window:{addEventListener:(_,callback)=>messages.push(callback)},
    fetch:async()=>({ok:true,json:async()=>({success:true,manifest:'hash',total_files:32,course_name:'Course',parts:[instruction(20),instruction(12,20)]})})
  });
  vm.runInContext('let lectureDownloadRequest=0; let lectureDownloadAttempt=0; let lectureDownloadSingle=null; let lectureDownloadProxyCheck=null; let lectureDownloadResetTimer=null; const lectureDownloadFrames=new Map();\n'+source.slice(start,end),context);
  const checkProxy = context.ensureLectureDownloadProxy;
  context.ensureLectureDownloadProxy = async () => {};
  return {context,submissions,nodes,messages,instruction,checkProxy,runReset:()=>{const timers=[...resetTimers.values()];resetTimers.clear();timers.forEach(fn=>fn());}};
}
test('no automatic download; explicit clicks use independent targets and survive panel close',async()=>{
  const {context,submissions,nodes,messages}=setup();
  await context.downloadAllAsZip('Fall','CS101');
  assert.equal(submissions.length,0);
  const rows=nodes.lectureDownloadParts.children;
  rows[0].querySelector('button').click();
  rows[1].querySelector('button').click();
  assert.equal(submissions.length,2);
  assert.notEqual(submissions[0].target,submissions[1].target);
  assert.equal(submissions[0].method,'POST');
  nodes.lectureDownloadClose.click();
  assert(nodes.lectureDownloadPanel.hidden);
  const frames=context.document.body.children.filter(node=>node.tagName==='iframe');
  assert.equal(frames.length,2);
  const status=rows[0].querySelector('.lecture-part-status');
  messages[0]({source:frames[0].contentWindow,origin:'https://evil.test',data:{type:'lecture-zip-error',error:'evil'}});
  assert(!status.textContent.includes('evil'));
  messages[0]({source:frames[0].contentWindow,origin:'https://downloads.example.test',data:{type:'lecture-zip-error',error:'Choose smaller parts.'}});
  assert.equal(status.textContent,'Choose smaller parts.');
});
test('expired tokens refresh instructions but require a second click',async()=>{
  const {context,submissions,nodes,instruction}=setup();
  const old=instruction(20); old.expires_at=0;
  let calls=0;
  context.fetch=async()=>({ok:true,json:async()=>({success:true,manifest:'hash',total_files:1,course_name:'Course',parts:calls++?[instruction(20)]:[old,instruction(12,20)]})});
  await context.downloadAllAsZip('Fall','CS101');
  nodes.lectureDownloadParts.children[0].querySelector('button').click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(submissions.length,0);
  nodes.lectureDownloadParts.children[0].querySelector('button').click();
  assert.equal(submissions.length,1);
});
test('smaller parts preserve selection and no Cloud Run fallback is offered',async()=>{
  const {context,submissions,nodes,instruction}=setup();
  await context.downloadAllAsZip('Fall','CS101');
  let query;
  context.fetch=async url=>{
    query=new URL(url,'https://lmsplus.test').searchParams;
    return {ok:true,json:async()=>({success:true,manifest:'hash',parts:[instruction(10),instruction(10,10)]})};
  };
  const row=nodes.lectureDownloadParts.children[0];
  row.querySelectorAll('button').find(button=>button.textContent==='Smaller parts').click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(query.get('start'),'0'); assert.equal(query.get('end'),'20');
  assert.equal(query.get('part_size'),'10'); assert.equal(query.get('manifest'),'hash');
  assert.equal(submissions.length,0);
  assert(!source.includes('fallback_url')); assert(!source.includes('.blob()'));
});
test('changed manifest and daily limits stay actionable',async()=>{
  const {context,nodes}=setup();
  await context.downloadAllAsZip('Fall','CS101');
  context.fetch=async()=>({ok:false,status:409,json:async()=>({code:'manifest_changed',error:'Lecture list changed.'})});
  nodes.lectureDownloadParts.children[0].querySelectorAll('button').find(button=>button.textContent==='Smaller parts').click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(nodes.lectureDownloadReview.hidden,false);
  assert.equal(nodes.lectureDownloadReview.textContent,'Review updated parts');
  context.fetch=async()=>({ok:false,status:429,json:async()=>({})});
  await context.downloadAllAsZip('Fall','CS101');
  assert.match(nodes.lectureDownloadFeedback.textContent,/limit reached/);
  assert(nodes.lectureDownloadPanel.hidden);
});

test('single ZIP starts directly, stays hidden, and caches a native retry', async()=>{
  const {context,nodes,submissions,instruction}=setup();
  let calls=0;
  context.fetch=async()=>{calls++; return {ok:true,json:async()=>({success:true,manifest:'hash',total_files:9,course_name:'Course',parts:[instruction(9)]})};};
  await context.downloadAllAsZip('Fall','CS101');
  assert.equal(submissions.length,1);
  assert.equal(nodes.lectureDownloadPanel.hidden,true);
  assert.equal(nodes.lectureDownloadParts.children.length,0);
  await context.downloadAllAsZip('Fall','CS101');
  assert.equal(submissions.length,2);
  assert.equal(calls,1);
});
test('single ZIP errors offer recovery without exposing the split notice', async()=>{
  const {context,nodes,messages,instruction}=setup();
  context.fetch=async()=>({ok:true,json:async()=>({success:true,manifest:'hash',total_files:9,parts:[instruction(9)]})});
  await context.downloadAllAsZip('Fall','CS101');
  const frame=context.document.body.children.find(node=>node.tagName==='iframe');
  messages[0]({source:frame.contentWindow,origin:'https://downloads.example.test',data:{type:'lecture-zip-error',error:'File too large.'}});
  assert(nodes.lectureDownloadPanel.hidden);
  assert.equal(nodes.lectureDownloadRecovery.hidden,false);
  assert.equal(nodes.lectureDownloadFeedback.textContent,'File too large.');
});

test('old Worker fails visibly before requesting download instructions; upgrade retry succeeds', async()=>{
  const {context,nodes,submissions,checkProxy,instruction}=setup();
  context.ensureLectureDownloadProxy=checkProxy;
  let updated=false, probes=0, instructions=0;
  context.fetch=async(url,options)=>{
    if(url.startsWith('https://downloads.example.test')) {
      probes++;
      assert.equal(options.method,'POST');
      assert.equal(options.body.get('token'),'compatibility-probe');
      return {status:updated?401:400,headers:{get:()=>updated?'text/html':'text/plain'},
        text:async()=>updated?'<script>lecture-zip-error</script>':'Missing token'};
    }
    instructions++;
    return {ok:true,json:async()=>({success:true,parts:[instruction(9)]})};
  };
  await context.downloadAllAsZip('Fall','CS101');
  assert.match(nodes.lectureDownloadFeedback.textContent,/Proxy update required/);
  assert.equal(instructions,0); assert.equal(submissions.length,0);
  updated=true;
  await context.downloadAllAsZip('Fall','CS101');
  assert.equal(probes,2); assert.equal(instructions,1); assert.equal(submissions.length,1);
});
test('compatible Worker probe is cached; network failure remains retryable', async()=>{
  const {context,checkProxy}=setup();
  let calls=0;
  context.fetch=async()=>{calls++; throw new Error('offline');};
  await assert.rejects(checkProxy(),/unreachable/);
  context.fetch=async()=>{calls++; return {status:401,headers:{get:()=>'text/html'},text:async()=>'<script>lecture-zip-error</script>'};};
  await checkProxy(); await checkProxy();
  assert.equal(calls,2);
});

test('motion follows preparation and handoff; reduced motion skips spatial effects', async()=>{
  const {context,nodes,instruction}=setup();
  let resolve;
  context.fetch=()=>new Promise(done=>{resolve=done;});
  let iconRuns=0, statusRuns=0;
  context.downloadAllBtn.querySelector=()=>({animate(){iconRuns++;}});
  nodes.lectureDownloadFeedback.animate=()=>{statusRuns++;};
  const request=context.downloadAllAsZip('Fall','CS101');
  assert.equal(nodes.lectureDownloadToolbar.attributes['data-download-state'],'preparing');
  await new Promise(done=>setImmediate(done));
  resolve({ok:true,json:async()=>({success:true,parts:[instruction(9)]})});
  await request;
  assert.equal(nodes.lectureDownloadToolbar.attributes['data-download-state'],'handoff');
  assert.equal(iconRuns,1);
  const prior=statusRuns;
  context.window.matchMedia=()=>({matches:true});
  context.setLectureDownloadMotion('handoff');
  assert.equal(iconRuns,1); assert.equal(statusRuns,prior);
  context.setLectureDownloadMotion('error');
  assert.equal(nodes.lectureDownloadToolbar.attributes['data-download-state'],'error');
});

test('handoff lives inside button for three seconds; errors cancel reset', async()=>{
  const {context,nodes,instruction,runReset,messages}=setup();
  let resolve;
  context.fetch=()=>new Promise(done=>{resolve=done;});
  const request=context.downloadAllAsZip('Fall','CS101');
  assert.equal(nodes.lectureDownloadButtonLabel.textContent,'Preparing…');
  assert.equal(nodes.lectureDownloadFeedback.textContent,'');
  await new Promise(done=>setImmediate(done));
  resolve({ok:true,json:async()=>({success:true,parts:[instruction(9)]})});
  await request;
  assert.equal(nodes.lectureDownloadButtonLabel.textContent,'Sent to browser');
  assert.equal(nodes.lectureDownloadFeedback.textContent,'');
  assert.equal(nodes.lectureDownloadAnnouncement.textContent,'Sent to browser.');
  runReset();
  assert.equal(nodes.lectureDownloadButtonLabel.textContent,'Download All');
  assert.equal(nodes.lectureDownloadToolbar.attributes['data-download-state'],'idle');
  await context.downloadAllAsZip('Fall','CS101');
  const frames=context.document.body.children.filter(el=>el.tagName==='iframe');
  messages[0]({source:frames.at(-1).contentWindow,origin:'https://downloads.example.test',data:{type:'lecture-zip-error',error:'Session expired. Retry.'}});
  runReset();
  assert.equal(nodes.lectureDownloadToolbar.attributes['data-download-state'],'error');
  assert.equal(nodes.lectureDownloadFeedback.textContent,'Session expired. Retry.');
  assert.equal(nodes.lectureDownloadButtonLabel.textContent,'Download All');
});
