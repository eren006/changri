(function(root){
'use strict';
function distance(ms){return 45+185*Math.min(1,Math.max(0,ms)/1200);}
function landed(x,p){return Math.abs(x-p.x)<=p.w/2-5;}
function mount(o){
var cv=document.createElement('canvas');cv.width=cv.height=360;cv.className='gm-canvas';cv.tabIndex=0;cv.setAttribute('aria-label','跳一跳：按住画面或空格蓄力，松开起跳');o.board.insertBefore(cv,o.over);
var ctx=cv.getContext('2d'),active=false,charging=false,charge=0,raf=0,last=0,flight=null,x=90,from,target,score=0,pointer=null,pausedAt=0;
var css=getComputedStyle(o.board),ink=css.getPropertyValue('--text').trim()||'#493c67',accent=css.getPropertyValue('--mine').trim()||'#8872b3';
function next(){return {x:185+Math.random()*95,w:Math.max(44,78-score)};}
function cancel(){charging=false;charge=0;pointer=null;}
function stop(){active=false;cancelAnimationFrame(raf);cancel();}
function draw(now){
ctx.clearRect(0,0,360,360);
[from,target].forEach(function(p,i){ctx.fillStyle=i?accent:'#aaa';ctx.fillRect(p.x-p.w/2,255,p.w,45);ctx.globalAlpha=.18;ctx.fillRect(p.x-p.w/2,270,p.w,30);ctx.globalAlpha=1;});
var y=255;
if(flight){
var t=Math.min(1,(now-flight.at)/650);x=90+flight.d*t;y-=Math.sin(t*Math.PI)*110;
if(t===1){flight=null;if(!landed(x,target)){active=false;o.finish('差一点点！再试一次');}
else{var perfect=Math.abs(x-target.x)<9;score+=perfect?2:1;o.setScore(score);o.status.textContent=perfect?'正中中心！+2 分':'稳稳落地！+1 分';from={x:90,w:target.w};target=next();x=90;}}}
var squash=charge/1200*7;ctx.fillStyle=ink;ctx.beginPath();ctx.arc(x,y-32+squash,9,0,Math.PI*2);ctx.fill();ctx.fillRect(x-9,y-23+squash,18,23-squash);
ctx.fillStyle='#aaa';ctx.fillRect(75,325,210,6);ctx.fillStyle=accent;ctx.fillRect(75,325,210*charge/1200,6);
ctx.fillStyle=ink;ctx.font='13px sans-serif';ctx.textAlign='center';ctx.fillText(charging?'松手起跳':'按住蓄力 · 松开跳跃',180,352);
}
function frame(now){if(!active)return;if(!document.hidden){if(charging)charge=Math.min(1200,charge+(last?now-last:0));draw(now);}last=now;if(active)raf=requestAnimationFrame(frame);}
function press(){if(!active||flight||charging||document.hidden)return;charging=true;charge=0;last=performance.now();}
function release(){if(!charging)return;charging=false;flight={at:performance.now(),d:distance(charge)};charge=0;}
cv.addEventListener('pointerdown',function(e){if(!e.isPrimary||e.button!==0)return;e.preventDefault();cv.focus();pointer=e.pointerId;cv.setPointerCapture(pointer);press();});
cv.addEventListener('pointerup',function(e){if(e.pointerId===pointer){release();pointer=null;}});
cv.addEventListener('pointercancel',cancel);cv.addEventListener('lostpointercapture',cancel);
document.addEventListener('keydown',function(e){if(e.code!=='Space'||!active||/INPUT|TEXTAREA|BUTTON|SELECT/.test(e.target.tagName))return;e.preventDefault();if(!e.repeat)press();});
document.addEventListener('keyup',function(e){if(e.code==='Space'&&charging){e.preventDefault();release();}});
window.addEventListener('blur',cancel);
document.addEventListener('visibilitychange',function(){cancel();if(document.hidden)pausedAt=performance.now();else{if(flight&&pausedAt)flight.at+=performance.now()-pausedAt;pausedAt=0;last=0;}});
return {stop:stop,start:function(){stop();score=0;o.setScore(0);from={x:90,w:80};target=next();x=90;flight=null;last=0;pausedAt=document.hidden?performance.now():0;active=true;o.status.textContent='落地得 1 分，正中中心得 2 分。切到后台自动暂停。';draw(performance.now());raf=requestAnimationFrame(frame);}};
}
root.PhoneJump={mount:mount,distance:distance,landed:landed};
if(typeof module!=='undefined')module.exports=root.PhoneJump;
})(typeof window!=='undefined'?window:globalThis);
