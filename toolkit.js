/* Declarative tool cards. Model content is always text, never executable markup. */
(() => {
  const node = (tag, text, cls) => { const e=document.createElement(tag); if(text)e.textContent=text; if(cls)e.className=cls; return e; };
  const button = (text, action) => { const b=node('button',text,'tool-button'); b.type='button'; b.onclick=action; return b; };
  window.ChronosTools = {
    render(artifacts, chatId) {
      const group=node('div',null,'tool-cards');
      (artifacts||[]).forEach(a=>{
        const card=node('section',null,'tool-card');card.append(node('h3',a.title||a.type));
        const items=a.items||[];
        const citation=i=> {if(i.source_ids?.length)card.append(node('small','Course passages: '+i.source_ids.join(', '),'tool-muted'));};
        if(a.type==='sources') {
          (a.sources||[]).forEach(s=>{const d=node('details');d.append(node('summary',s.id+' · '+s.source+(s.page?' · page '+s.page:s.chunk?' · excerpt '+s.chunk:'')),node('blockquote',s.text));card.append(d);});
        } else if(a.type==='scratchpad') {
          const t=node('textarea');t.rows=6;t.placeholder='Try your reasoning here…';t.setAttribute('aria-label','Private scratchpad');
          card.append(t,node('small','Private scratch space. This is not sent to Chronos or saved when you leave.','tool-muted'));
        } else if(a.type==='report_gap') {
          card.append(node('p',a.text));const t=node('textarea');t.maxLength=300;t.rows=3;t.setAttribute('aria-label','Course learning difficulty');
          const status=node('p');status.setAttribute('role','status');
          const send=button('Send to my teacher',async()=>{send.disabled=true;try{await Chronos.apiJson('/learning-gap',{chat_id:chatId,message:t.value});status.textContent='Shared with your teacher.';t.disabled=true;}catch(e){status.textContent=e.message;send.disabled=false;}});
          card.append(t,send,status);
        } else if(['quiz','knowledge_check'].includes(a.type)) {
          items.forEach(i=>{const box=node('fieldset');box.append(node('legend',i.prompt));const result=node('p');result.setAttribute('role','status');
            i.options.forEach(o=>box.append(button(o,()=>{result.textContent=o===i.answer?'Correct.':'Try again and check the course passage.';})));
            box.append(result);card.append(box);citation(i);});
        } else if(a.type==='matching') {
          const choices=items.map(i=>i.answer).sort(()=>Math.random()-.5);
          items.forEach(i=>{const label=node('label',i.prompt);const select=node('select');select.append(new Option('Choose a match',''));choices.forEach(c=>select.append(new Option(c,c)));label.append(select);const result=node('span');result.setAttribute('role','status');select.onchange=()=>{result.textContent=select.value===i.answer?' Correct':' Try again';};card.append(label,result);citation(i);});
        } else if(a.type==='ordering') {
          const order=items.map((v,i)=>({v,i})).reverse();const list=node('ol');const result=node('p');result.setAttribute('role','status');
          const paint=()=>{list.replaceChildren();order.forEach((x,idx)=>{const li=node('li');li.append(node('span',x.v.prompt),button('↑',()=>{if(idx){[order[idx-1],order[idx]]=[order[idx],order[idx-1]];paint();}}),button('↓',()=>{if(idx<order.length-1){[order[idx+1],order[idx]]=[order[idx],order[idx+1]];paint();}}));li.querySelectorAll('button').forEach((b,j)=>b.setAttribute('aria-label',(j?'Move down: ':'Move up: ')+x.v.prompt));list.append(li);});};paint();
          card.append(list,button('Check order',()=>{result.textContent=order.every((x,i)=>x.i===i)?'Correct order.':'Keep going; check the sequence in your material.';}),result);items.forEach(citation);
        } else if(['flashcards','hints','guided_problem','socratic'].includes(a.type)) {
          items.forEach((i,n)=>{const d=node('details');d.append(node('summary',a.type==='flashcards'?i.prompt:'Step '+(n+1)),node('p',a.type==='flashcards'?i.answer:i.prompt));card.append(d);citation(i);});
        } else if(a.type==='table') {
          const wrap=node('div',null,'tool-table-wrap');const table=node('table');(a.rows||[]).forEach((r,n)=>{const tr=node('tr');r.forEach(c=>tr.append(node(n?'td':'th',c)));table.append(tr);});wrap.append(table);card.append(wrap);items.forEach(citation);
        } else if(a.type==='graph') {
          const rows=(a.rows||[]).filter(r=>r.length===2&&r[1].trim()!==''&&Number.isFinite(Number(r[1])));const max=Math.max(1,...rows.map(r=>Math.abs(Number(r[1]))));
          rows.forEach(r=>{const row=node('div',null,'tool-graph-row');row.append(node('span',r[0]+': '+r[1]));const bar=node('div',null,'tool-bar');bar.style.width=(Math.abs(Number(r[1]))/max*100)+'%';row.append(bar);card.append(row);});items.forEach(citation);
        } else if(['diagram','concept_map','timeline'].includes(a.type)) {
          const list=node('ol',null,'tool-diagram');items.forEach(i=>{const li=node('li',i.prompt+(i.answer?' — '+i.answer:''));list.append(li);});card.append(list);
          (a.edges||[]).forEach(e=>card.append(node('p',e.from+' → '+e.to+(e.label?' ('+e.label+')':''))));items.forEach(citation);
        } else {
          card.append(node('pre',a.text||items.map(i=>i.prompt).join('\n')));items.forEach(citation);
          if(a.type==='file')card.append(button('Download .txt',()=>{const url=URL.createObjectURL(new Blob([a.title+'\n\n'+a.text],{type:'text/plain;charset=utf-8'}));const link=node('a');link.href=url;link.download='chronos-study.txt';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}));
        }
        group.append(card);
      });
      return group;
    }
  };
})();
