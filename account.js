/* Firebase account settings shared by teacher and student pages. */
(() => {
  const trigger=document.createElement('button');trigger.textContent='Account';trigger.className='account-trigger tool-button';trigger.type='button';
  const anchor=document.querySelector('[data-theme-toggle]');
  if(anchor){anchor.parentElement.append(trigger);trigger.classList.add('account-inline');}else document.body.append(trigger);
  const mobile=trigger.cloneNode(true);mobile.className='account-mobile tool-button';mobile.onclick=()=>trigger.click();document.body.append(mobile);
  trigger.onclick=async()=>{
    await Chronos.ready;const user=Chronos.auth.currentUser;if(!user)return;
    const dialog=document.createElement('dialog');dialog.className='account-dialog tool-card';
    const title=document.createElement('h2');title.textContent='Account settings';
    const email=document.createElement('p');email.textContent=user.email||'';
    const label=document.createElement('label');label.textContent='Display name';const name=document.createElement('input');name.value=user.displayName||'';name.maxLength=80;label.append(name);
    const status=document.createElement('p');status.setAttribute('role','status');
    const save=document.createElement('button');save.className='tool-button';save.textContent='Save name';save.onclick=async()=>{save.disabled=true;try{await user.updateProfile({displayName:name.value.trim()});status.textContent='Name saved.';}catch(e){status.textContent=Chronos.friendlyAuthError(e);}finally{save.disabled=false;}};
    const reset=document.createElement('button');reset.className='tool-button';reset.textContent='Send password reset email';reset.onclick=async()=>{reset.disabled=true;try{await Chronos.auth.sendPasswordResetEmail(user.email);status.textContent='Password reset email sent.';}catch(e){status.textContent=Chronos.friendlyAuthError(e);reset.disabled=false;}};
    const close=document.createElement('button');close.className='tool-button';close.textContent='Close';close.onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();
    dialog.append(title,email,label,save,reset,status,close);document.body.append(dialog);dialog.showModal();
  };
})();
