(function(root){
'use strict';
var W=360,H=360,PX=80,R=12,GRAV=900,FLAP=-290,SPEED=120,GAP=118,SPACING=190,PIPE_W=46;
function hits(by,p){
  var inX=PX+R>p.x&&PX-R<p.x+PIPE_W;
  return inX&&(by-R<p.gapY-GAP/2||by+R>p.gapY+GAP/2);
}
function mount(o){
var cv=document.createElement('canvas');cv.width=W;cv.height=H;cv.className='gm-canvas';cv.tabIndex=0;
cv.setAttribute('aria-label','飞鸽传书：点按画面或按空格扇一下翅膀');o.board.insertBefore(cv,o.over);
var ctx=cv.getContext('2d'),css=getComputedStyle(o.board),ink=css.getPropertyValue('--text').trim()||'#493c67',accent=css.getPropertyValue('--mine').trim()||'#8872b3';
var active=false,raf=0,last=0,y,vy,pipes,score,wing=0,flying=false;
function spawn(x){var p={x:x,gapY:70+Math.random()*(H-140),passed:false,letter:Math.random()<.35};pipes.push(p);}
function say(t){if(o.status)o.status.textContent=t;}
function end(){active=false;cancelAnimationFrame(raf);o.finish('没能把信送到');}
function flap(){if(!active||document.hidden)return;flying=true;vy=FLAP;wing=.15;}
function step(dt){
  if(!flying)return;
  vy+=GRAV*dt;y+=vy*dt;if(wing>0)wing-=dt;
  pipes.forEach(function(p){p.x-=SPEED*dt;});
  if(pipes[0].x+PIPE_W<0)pipes.shift();
  var tail=pipes[pipes.length-1];if(tail.x<W-SPACING)spawn(tail.x+SPACING);
  for(var i=0;i<pipes.length;i++){
    var p=pipes[i];
    if(hits(y,p))return end();
    if(p.letter&&Math.abs(PX-(p.x+PIPE_W/2))<R+10&&Math.abs(y-p.gapY)<GAP/2-8){p.letter=false;score+=2;o.setScore(score);say('接到信了！+2 分');}
    if(!p.passed&&p.x+PIPE_W<PX-R){p.passed=true;score+=1;o.setScore(score);}
  }
  if(y-R<0||y+R>H)end();
}
function draw(){
  ctx.clearRect(0,0,W,H);
  pipes.forEach(function(p){
    ctx.fillStyle=accent;ctx.globalAlpha=.55;
    ctx.fillRect(p.x,0,PIPE_W,p.gapY-GAP/2);ctx.fillRect(p.x,p.gapY+GAP/2,PIPE_W,H);
    ctx.globalAlpha=1;
    if(p.letter){ctx.font='20px serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText('✉️',p.x+PIPE_W/2,p.gapY);}
  });
  ctx.font='26px serif';ctx.textAlign='center';ctx.textBaseline='middle';
  ctx.save();ctx.translate(PX,y);ctx.rotate(Math.max(-.5,Math.min(.8,vy/500)));
  ctx.scale(wing>0?1.25:1,wing>0?1.25:1);ctx.fillText('🐦',0,0);ctx.restore();
  ctx.fillStyle=ink;ctx.font='13px sans-serif';ctx.textBaseline='alphabetic';ctx.fillText(flying?'穿过缝隙，接住信件':'点一下开始飞',W/2,20);
}
function frame(now){
  if(!active)return;
  if(!document.hidden){var dt=Math.min(.033,(now-(last||now))/1000);last=now;step(dt);if(active)draw();}
  else last=0;
  if(active)raf=requestAnimationFrame(frame);
}
cv.addEventListener('pointerdown',function(e){if(!e.isPrimary||e.button!==0)return;e.preventDefault();cv.focus();flap();});
document.addEventListener('keydown',function(e){
  if((e.code!=='Space'&&e.code!=='ArrowUp')||!active||e.repeat||/INPUT|TEXTAREA|BUTTON|SELECT/.test(e.target.tagName))return;
  e.preventDefault();flap();
});
document.addEventListener('visibilitychange',function(){last=0;});
function stop(){active=false;cancelAnimationFrame(raf);}
return {stop:stop,start:function(){
  stop();score=0;o.setScore(0);y=H/2;vy=0;wing=0;flying=false;pipes=[];spawn(W+20);last=0;active=true;
  say('穿过缝隙得 1 分，接住信件 +2 分。切到后台自动暂停。');draw();raf=requestAnimationFrame(frame);
}};
}
root.PhonePigeon={mount:mount,hits:hits};
if(typeof module!=='undefined')module.exports=root.PhonePigeon;
})(typeof window!=='undefined'?window:globalThis);
