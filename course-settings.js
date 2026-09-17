(() => {
  let revision=0;
  window.loadCourseSettings=async(classId)=>{
    const host=document.getElementById('courseSettings');if(!host)return;
    const generation=++revision;host.replaceChildren();if(!classId)return;
    host.textContent='Loading tutor settings…';
    try {
      const data=await Chronos.apiJson('/course-settings',{class_id:classId});if(generation!==revision)return;
      host.replaceChildren();
      const heading=document.createElement('h2');heading.textContent='Tutor rules & toolkits';host.append(heading);
      const note=document.createElement('p');note.textContent='Base rules start on. Toolkits start off. Changes apply to the next question. Custom rules below always apply, regardless of which passages match.';host.append(note);
      let policy=data.policy;
      const status=document.createElement('p');status.setAttribute('role','status');
      for(const [group,labels] of [['base',data.base_labels],['toolkits',data.toolkit_labels]]) {
        const field=document.createElement('fieldset');const legend=document.createElement('legend');legend.textContent=group==='base'?'Base rules':'Allow toolkits';field.append(legend);
        Object.entries(labels).forEach(([key,title])=>{
          const label=document.createElement('label');label.className='policy-switch';const input=document.createElement('input');input.type='checkbox';input.checked=policy[group][key];input.setAttribute('role','switch');
          const text=document.createElement('span');text.textContent=title;label.append(input,text);field.append(label);
          input.onchange=async()=>{
            const previous=policy[group][key];const next=input.checked;
            if(key==='teacher_only'&&!next&&!confirm('Allow knowledge outside teacher material? Chronos may then supply facts you have not provided or verified. Other tutoring rules still apply.')){input.checked=previous;return;}
            const updated=JSON.parse(JSON.stringify(policy));updated[group][key]=next;
            host.querySelectorAll('input').forEach(i=>i.disabled=true);status.textContent='Saving…';
            try{const saved=await Chronos.apiJson('/course-settings',{class_id:classId,policy:updated,confirm_general_knowledge:key==='teacher_only'&&!next});policy=saved.policy;status.textContent='Saved. Applies to the next question.';}
            catch(e){input.checked=previous;status.textContent=e.message;}
            finally{host.querySelectorAll('input').forEach(i=>i.disabled=false);}
          };
        });host.append(field);
      }
      const help=document.createElement('p');help.textContent='Study tools and file generation also require “Restrict study material generation” to be off. Source viewer shares retrieved course excerpts with students, never private rules. Learning-gap reports require student submission.';host.append(help,status);
    } catch(e) {if(generation===revision)host.textContent=e.message;}
  };
})();
