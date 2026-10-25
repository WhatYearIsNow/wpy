
/* ============================================================
   数据来源：本地接口 /api/*
   原来 POSTS 是内嵌的全量数组，20 万帖时 HTML 会超过 130 MB 打不开。
   现在页面只保留界面代码，数据在翻页/筛选时实时向后端取。
   VIEW 与 POSTS 指同一组数据（当前页），保持原有代码结构不变。
   ============================================================ */
var POSTS=[], VIEW=[], PAGE=0, SZ=12;
var TOTAL=0;                 // 当前条件下的总条数（后端给）
var LOADING=false;
var _lastLoad=null;          // 最近一次列表请求，供调用方等待筛选完成
var _EXPORTING=false;        // 导出进行中，防重复触发
var $=function(s){return document.querySelector(s)};

/* 已经打开过详情的帖子（id -> 完整数据）。
   详情是单独向 /api/post 取的，不在 POSTS（那只是列表当前页）里。
   分享/导出都要拿完整正文与评论，若只翻 POSTS 就会「不在当前页就啥也不做」，
   所以这里按 id 存一份，找不到再回落到 POSTS。*/
var POST_CACHE=Object.create(null);
function rememberPost(p){
  if(p && p.id) POST_CACHE[p.id]=p;
  if(Object.keys(POST_CACHE).length>80){       // 简单限容，避免长会话堆积
    var ks=Object.keys(POST_CACHE);
    for(var i=0;i<40;i++) delete POST_CACHE[ks[i]];
  }
  return p;
}
/* 按 id 找帖子：先看打开过的那份，再看列表当前页。*/
function findPost(id){
  id=Number(id);
  if(POST_CACHE[id]) return POST_CACHE[id];
  for(var i=0;i<POSTS.length;i++) if(POSTS[i].id===id) return POSTS[i];
  for(var j=0;j<VIEW.length;j++) if(VIEW[j].id===id) return VIEW[j];
  return null;
}

/* 统一的接口请求（带内存缓存）。
   看板经常来回切换筛选条件，同样的查询没必要重复请求。
   缓存只在本次会话有效，刷新即失效。*/var _CACHE=Object.create(null);
function api(path, params, noCache){
  var qs=params?('?'+Object.keys(params).filter(function(k){
    var v=params[k];return v!==undefined&&v!==null&&v!==''
  }).map(function(k){return encodeURIComponent(k)+'='+encodeURIComponent(params[k])}).join('&')):'';
  var key=path+qs;
  if(!noCache && _CACHE[key]) return Promise.resolve(_CACHE[key]);
  // 超时：用户统计/慢查询卡住时至少能失败，而不是一直“统计中”拖死体验
  var ctl=(typeof AbortController!=='undefined')?new AbortController():null;
  var timer=ctl?setTimeout(function(){ctl.abort()},12000):null;
  return fetch(key, ctl?{signal:ctl.signal}:undefined).then(function(r){
    if(r.status===401){
      var badge=$('#rolebadge');
      if(badge) badge.innerHTML='登录已过期 <a href="login">重新登录</a> · <a href="./">游客浏览</a>';
      throw new Error('登录已过期，请重新登录或以游客浏览');
    }
    if(!r.ok) throw new Error('HTTP '+r.status);
    return r.json();
  }).then(function(d){
    // 只缓存列表/元数据这类结果；帖子详情可能变（评论在补），不缓存
    if(!noCache && path!=='api/post' && !d.personalPending){
      _CACHE[key]=d;
      if(Object.keys(_CACHE).length>200) _CACHE=Object.create(null);  // 简单限容
    }
    return d;
  }).catch(function(e){
    if(e && e.name==='AbortError') throw new Error('请求超时（12秒）');
    throw e;
  }).then(function(d){
    if(timer) clearTimeout(timer);
    return d;
  }, function(e){
    if(timer) clearTimeout(timer);
    throw e;
  });
}
function esc(s){return (s==null?'':String(s)).replace(/[&<>"']/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
/* 列表/详情展示用 thumb；缩放、导出前把地址换成 origin。
   服务版路径是 /img/thumb/...，静态 CDN 是 .../download/thumb/... */
function fullUrl(u){
  if(!u) return u;
  return String(u).replace('/img/thumb/','/img/origin/')
                  .replace('/download/thumb/','/download/origin/');
}
// uid 传了就渲染成可点击的头像（点一下看该用户全部帖子）
function avatarHtml(src,name,cls,uid){
  var clickable = (uid!==undefined && uid!==null && uid>0);
  var attr = clickable
    ? ' data-uid="'+uid+'" title="查看 TA 的全部帖子"'
    : '';
  var tag = clickable ? 'avatar-link' : '';
  if(src){
    return '<img class="avatar '+tag+' '+(cls||'')+'" src="'+esc(src)+'" loading="lazy"'
      +' alt=""'+attr+' onerror="this.style.display=\'none\'">';
  }
  var ch=(name||'?').slice(0,1);
  var hue=0; for(var i=0;i<(name||'').length;i++) hue=(hue*31+name.charCodeAt(i))%360;
  return '<div class="avatar ph '+tag+' '+(cls||'')+'" style="background:hsl('+hue+',45%,58%)"'
    +attr+'>'+esc(ch)+'</div>';
}
/* 高亮搜索命中的关键词。
   不用正则：关键词里可能带 . * ( ) 等字符，多层转义很容易写错（踩过坑）。
   改用「按出现位置切片拼接」，逻辑直白且不会因转义出错。*/
function highlight(text){
  var out=esc(String(text==null?'':text));
  if(!SEARCH_TERMS || !SEARCH_TERMS.length) return out;
  SEARCH_TERMS.forEach(function(t){
    t=String(t||'');
    if(!t) return;
    var low=out.toLowerCase(), key=t.toLowerCase();
    if(key.length<1) return;
    var buf='', pos=0, at, guard=0;
    while((at=low.indexOf(key,pos))>=0){
      buf += out.slice(pos,at) + '<mark class="hl">' + out.substr(at,key.length) + '</mark>';
      pos = at + Math.max(1, key.length);
      // 防御：避免异常输入导致死循环（页面无响应）
      if(++guard>200) break;
    }
    if(pos>0) out = buf + out.slice(pos);
  });
  return out;
}

function typeColor(t){var m=__COLORS__;return m[t]||'__DEFAULT_COLOR__'}

/* ============================================================
   加载态：骨架屏 + 顶部进度条
   ============================================================ */
/* 顶部细进度条：任何超过 ~180ms 的操作都显示，短操作不闪。
   延迟显示是为了避免"请求 50ms 就回来"时进度条一闪而过，反而更晃眼。*/
var _pbarTimer=null;
/* ---- 角色与动作（服务端代理） ---- */
var ROLE='guest', CSRF='';
/* 触屏设备没有 hover：关注浮层、用户卡片都要走点击路径 */
var IS_TOUCH=('ontouchstart' in window)||(navigator.maxTouchPoints||0)>0;
var BASE_TITLE=document.title;
function bootRole(){
  return fetch('api/me').then(function(r){ return r.json() }).then(function(d){
    ROLE=(d && d.role==='user') ? 'user' : 'guest';
    CSRF=(d && d.csrf)||'';
  }).catch(function(){ ROLE='guest'; }).then(function(){
    // 先移除再添加：页面刚加载时 body 带默认 role-guest（见 HTML 模板），
    // 角色确认前游客不应看到任何登录用户入口
    document.body.classList.remove('role-guest','role-user');
    document.body.classList.add('role-'+ROLE);
    var b=$('#rolebadge');
    if(b) b.innerHTML=(ROLE==='user')
      ? '已登录 <button type="button" class="rolelink" onclick="signOut()">退出</button>'
      : '游客 <a href="login">登录</a>';
    loadLocalFav();
    return loadFollow();   // 角色定了再决定关注列表来源
  });
}
function postForm(path,data){
  var body=Object.keys(data||{}).map(function(k){
    return encodeURIComponent(k)+'='+encodeURIComponent(data[k]==null?'':data[k]);
  }).join('&');
  return fetch(path,{method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded','X-WPY-CSRF':CSRF},body:body})
    .then(function(r){
      return r.json().catch(function(){ return {} }).then(function(d){
        if(r.status===401){
          var badge=$('#rolebadge');
          if(badge) badge.innerHTML='登录已过期 <a href="login">重新登录</a> · <a href="./">游客浏览</a>';
        }
        return d;
      });
    });
}
function signOut(){
  postForm('logout',{}).then(function(d){
    if(d && d.ok) location.replace('./');
    else toast((d && d.error)||'退出失败，请重试');
  }).catch(function(){ toast('退出失败：服务不可达'); });
}
/* 湖底写接口不幂等：重复执行已生效的操作会返回「数据库错误」。
   把这句内部话翻译成访客能懂、也知道下一步做什么的提示。 */
function friendlyActErr(msg, fallback){
  msg=String(msg||'');
  if(msg.indexOf('数据库错误')>=0) return '操作可能已生效或已撤销，刷新页面查看最新状态';
  return msg || fallback;
}
function actbarHtml(p){
  // 详情页：收藏/点赞按钮直接并入 detail-meta 行（与 分享/图片 同一行）
  var favOn=(ROLE==='user') ? !!p.fav : !!LOCALFAV[String(p.id)];
  var pending=ROLE==='user' && p.personalPending;
  return '<button class="act'+(favOn?' on':'')+'" id="favbtn-'+p.id+'" '+
      (pending?'disabled title="个人收藏暂时无法同步，请稍后刷新" ':'')+
      'onclick="toggleFav('+p.id+')">'+(favOn?'★ 已收藏':'☆ 收藏')+'</button>'+
    '<button class="act user-only" id="likebtn-'+p.id+'" '+
      'onclick="toggleLike('+p.id+')">&#128077; 点赞'+(p.lk?' '+p.lk:'')+'</button>';
}
function toggleFav(id){
  var key=String(id);
  var b0=$('#favbtn-'+id);
  if(b0 && b0.disabled) return;         // 请求在途，防连点重复提交
  if(ROLE!=='user'){
    var on=!!LOCALFAV[key];
    if(on){ delete LOCALFAV[key]; toast('已取消收藏（仅本机）') }
    else{ LOCALFAV[key]=1; toast('已收藏（仅保存在本机）') }
    saveLocalFav();
    if(b0){ b0.classList.toggle('on',!on);
            b0.textContent=(!on)?'★ 已收藏':'☆ 收藏'; }
    return;
  }
  var p=POST_CACHE[id]||findPost(id)||{};
  var op=p.fav?0:1;  // op: 1=收藏 0=取消（实测语义）
  if(b0) b0.disabled=true;
  postForm('api/wpy/fav',{post_id:id,op:op}).then(function(d){
    if(d && d.ok){
      _CACHE=Object.create(null);
      p.fav=op?1:0;   // op=1 表示刚执行了收藏（与 toggleLike 同一套语义）
      var b=$('#favbtn-'+id);
      if(b){ b.classList.toggle('on',!!p.fav);
             b.textContent=p.fav?'★ 已收藏':'☆ 收藏'; }
      toast(op?'已收藏（存在你的湖底账号里）':'已取消收藏');
    } else toast(friendlyActErr(d && d.error, '收藏失败'));
  }).catch(function(){ toast('收藏失败：服务不可达') })
    .then(function(){ if(b0) b0.disabled=false; });
}
function toggleLike(id){
  var p=POST_CACHE[id]||findPost(id)||{};
  var btn=$('#likebtn-'+id);
  if(btn && btn.disabled) return;       // 请求在途，防连点重复提交
  var on=btn?btn.classList.contains('on'):false;
  var op=on?0:1;  // op: 1=点赞 0=取消（实测语义）
  if(btn) btn.disabled=true;
  postForm('api/wpy/like',{post_id:id,op:op}).then(function(d){
    if(d && d.ok){
      p.lk=Math.max(0,(p.lk||0)+(op?1:-1));
      if(btn){ btn.classList.toggle('on',!!op);
               btn.textContent='\uD83D\uDC4D 点赞'+(p.lk?' '+p.lk:''); }
      toast(op?'已点赞':'已取消点赞');
    } else toast(friendlyActErr(d && d.error, '点赞失败'));
  }).catch(function(){ toast('点赞失败：服务不可达') })
    .then(function(){ if(btn) btn.disabled=false; });
}
function toggleFloorLike(fid,btn){
  if(btn.disabled) return;              // 请求在途，防连点重复提交
  btn.disabled=true;
  var op=btn.classList.contains('on')?1:0;
  postForm('api/wpy/floor_like',{floor_id:fid,op:op}).then(function(d){
    if(d && d.ok) btn.classList.toggle('on',!op);
    else toast(friendlyActErr(d && d.error, '操作失败'));
  }).catch(function(){ toast('操作失败：服务不可达') })
    .then(function(){ btn.disabled=false; });
}
/* ---- 评论（登录用户）：顶层评论 POST floor；楼中楼 POST floor/reply ----
   数据同步有延迟（评论由定时抓取入库），发出后先本地清空并提示。 */
var REPLY_TO=null;        // {fid, name} 楼中楼回复目标；null=发顶层评论
function replyToFloor(fid,name){
  REPLY_TO={fid:fid,name:name||''};
  var chip=document.getElementById('reply-target');
  if(chip){ chip.style.display='flex';
    var nm=document.getElementById('reply-target-name');
    if(nm) nm.textContent='回复 '+(name||('楼层'+fid))+'：'; }
  var ta=document.querySelector('[id^=replytext-]');
  if(ta) ta.focus();
}
function clearReplyTo(){
  REPLY_TO=null;
  var chip=document.getElementById('reply-target');
  if(chip) chip.style.display='none';
}
function sendReply(id){
  var t=$('#replytext-'+id); var c=((t&&t.value)||'').trim();
  if(!c){ toast('先写点内容'); return; }
  if(REPLY_TO){
    var rt=REPLY_TO;
    busyOn();
    // 湖底 floor 接口要求恒带 images 字段（空也要带），缺了返回 500
    postForm('api/wpy/floor_reply',{reply_to_floor:rt.fid,content:c,images:''})
      .then(function(d){
        if(d && d.ok){
          toast('回复已发出（数据同步后可见）');
          clearReplyTo(); t.value='';
        } else toast(friendlyActErr(d && d.error, '回复失败'));
      }).catch(function(){ toast('回复失败：服务不可达') })
      .then(function(){ busyOff() });
    return;
  }
  busyOn();
  postForm('api/wpy/reply',{post_id:id,content:c,images:''}).then(function(d){
    if(d && d.ok){
      toast('评论已发出（数据同步后可见）');
      var p=POST_CACHE[id]; if(p){ p.cm=(p.cm||0)+1; p.got=1; }
      t.value='';
    } else toast(friendlyActErr(d && d.error, '评论失败'));
  }).catch(function(){ toast('评论失败：服务不可达') }).then(function(){ busyOff() });
}
/* ---- 发帖（登录用户） ---- */
var PTYPES=null;
function openComposer(){
  if(PTYPES){ renderComposer(PTYPES); return; }
  api('api/wpy/posttypes',null,true).then(function(d){
    // 接口返回 {ok,data:{list:[...]}}：这里兼容直接给数组的情况
    var arr=(d && d.ok && d.data) ? (d.data.list||d.data) : null;
    PTYPES=(arr&&arr.length)?arr:null;
    renderComposer(PTYPES);
  }).catch(function(){ renderComposer(null); });
}
function escAttr(s){ return esc(s).replace(/"/g,'&quot;') }
function renderComposer(types){
  var opts='';
  if(types && types.length){
    types.forEach(function(t){
      var id=t.id||t.type_id||t.ID||'';
      var nm=t.name||t.title||t.type||('板块 '+id);
      if(id!=='') opts+='<option value="'+escAttr(String(id))+'">'+esc(nm)+'</option>';
    });
  }
  if(!opts) opts='<option value="2">湖底</option><option value="3">学习</option>'+
    '<option value="4">交友</option><option value="13">美食</option>'+
    '<option value="1">校务</option>';
  $('#sheet').innerHTML='<div class="sheet"><div class="head"><div class="headin">'+
    '<button class="back" onclick="closePost()"><span>取消</span></button>'+
    '<span class="ttl">发帖</span></div></div>'+
    '<div class="body"><div class="replybox" style="margin-top:0">'+
    '<select id="cptype">'+opts+'</select>'+
    '<select id="cpcampus">'+
      '<option value="0">双校区</option><option value="1">卫津路</option>'+
      '<option value="2">北洋园</option></select>'+
    '<input type="text" id="cptitle" placeholder="标题">'+
    '<textarea id="cpcontent" placeholder="正文"></textarea>'+
    '<div class="cp-hint">将以你自己的湖底账号发布</div>'+
    '<div class="row"><button class="act" onclick="submitPost()">发布</button></div>'+
    '</div></div></div>';
  $('#modal').classList.add('on');
  document.body.style.overflow='hidden';
}
function submitPost(){
  var type=$('#cptype').value,
      title=($('#cptitle').value||'').trim(),
      content=($('#cpcontent').value||'').trim(),
      campus=$('#cpcampus').value;
  if(!title || !content){ toast('标题和正文都要写'); return; }
  busyOn();
  postForm('api/wpy/post',{type:type,title:title,content:content,campus:campus})
    .then(function(d){
      if(d && d.ok){ _CACHE=Object.create(null); toast('发布成功'); closePost(); applyFilter(); }
      else toast((d && d.error) || '发布失败');
    }).catch(function(){ toast('发布失败：服务不可达') }).then(function(){ busyOff() });
}
function busyOn(){
  clearTimeout(_pbarTimer);
  _pbarTimer=setTimeout(function(){
    var b=document.getElementById('pbar');
    if(b) b.classList.add('on');
  },180);
}
function busyOff(){
  clearTimeout(_pbarTimer);
  var b=document.getElementById('pbar');
  if(b) b.classList.remove('on');
}

/* 骨架卡片：占住真实卡片的大致高度，让"即将出现内容"可预期 */
function skeletonHtml(n){
  var one='<div class="skel">'+
    '<div class="sk-row"><div class="sk sk-av"></div>'+
      '<div class="sk sk-nm"></div><div class="sk sk-tm"></div></div>'+
    '<div class="sk sk-ttl"></div>'+
    '<div class="sk sk-tx"></div><div class="sk sk-tx s2"></div>'+
    '<div class="sk sk-tx s3"></div>'+
    '<div class="sk sk-meta"></div></div>';
  return one.repeat ? one.repeat(n||4) : new Array((n||4)+1).join(one);
}

/* 兜底：给"卡住"的图片收尾。
   实测发现，同一批插进来的图片里如果出现重复 URL，Chrome 的
   loading=lazy 会把后续相同地址的请求合并掉，那些 <img> 既不触发 load
   也不触发 error，complete 一直是 false —— 占位底色的扫光动画就永远停不下来，
   看起来像"这张图一直在加载"。
   这里在列表渲染后延迟扫一遍：凡是已经确定有尺寸（或已失败）的，
   补上 data-loaded 让动画停下。真正的加载中不会被误伤，
   因为它们此时 naturalWidth 还是 0 且 complete 为 false。*/
function settleImages(root){
  var scope=root||document;
  setTimeout(function(){
    var imgs=scope.querySelectorAll('img:not([data-loaded])');
    for(var i=0;i<imgs.length;i++){
      var im=imgs[i];
      // complete=true 说明浏览器已经不管它了（成功或失败），可以收尾
      if(im.complete || (im.naturalWidth>0)){
        im.setAttribute('data-loaded','1');
      }
    }
  },2500);
}

function imgsHtml(imgs){
  if(!imgs||!imgs.length) return '';
  var n=imgs.length;
  var use = n>3? imgs.slice(0,3): imgs;
  var extra = n-use.length;
  var cls = (use.length<=1 && extra===0) ? 'imgs one' : 'imgs multi';
  var html='<div class="'+cls+'">';
  for(var i=0;i<use.length;i++){
    var u=use[i];
    html += '<img src="'+esc(u)+'" loading="lazy" decoding="async" alt=""'
      + ' data-full="'+esc(fullUrl(u))+'"'
      + ' onload="this.setAttribute(\'data-loaded\',\'1\')"'
      + ' onerror="this.setAttribute(\'data-loaded\',\'1\');'
      + 'this.setAttribute(\'data-failed\',\'1\')">';
  }
  if(extra>0) html += '<span class="more-n">+'+extra+'</span>';
  html += '</div>';
  return html;
}
function cardHtml(p){
  // 列表摘要过长会拖垮搜索结果页的渲染
  var body=p.c||'';
  if(body.length>320) body=body.slice(0,320)+'…';
  return '<article class="card" data-id="'+p.id+'">'+
    '<div class="who">'+avatarHtml(p.av,p.a,'',p.uid)+
      '<span class="nm clickable" data-uid="'+p.uid+'" data-mp="#MP'+p.id+
      '" title="点击复制 MP 号">'+
      esc(p.a)+'</span>'+
      (p.lv?'<span class="lv">Lv.'+p.lv+'</span>':'')+
      '<span class="tm">'+esc(p.d)+'</span></div>'+
    (p.t?'<div class="title">'+highlight(p.t)+'</div>':'')+
    (body?'<div class="content">'+highlight(body)+'</div>':'')+
    imgsHtml(p.imgs)+
    '<div class="meta">'+
      (p.tn?'<span class="tag board" style="background:'+typeColor(p.ty)+'">'+
        esc(p.tn)+'</span>':'')+
      (p.ess?'<span class="tag essence">精华</span>':'')+
      ((ROLE==='user'&&p.mine)?'<span class="tag mine">我发的</span>':'')+
      ((ROLE==='user'&&p.fav)?'<span class="tag fav">我收藏</span>':'')+
      (p.gone?'<span class="tag gone" title="详情接口取不到正文，帖子可能已被删除或隐藏">正文未取到</span>':'')+
      (p.tag?'<span class="tag label">'+esc(p.tag)+'</span>':'')+
      (p.etag==='top'?'<span class="tag top">置顶</span>':'')+
      (p.st==='是'?'<span class="tag solved">已解决</span>':'')+
      '<span class="counts"><span>浏览 <b>'+p.vs+'</b></span>'+
      '<span>评论 <b>'+p.cm+'</b>'+((p.got||!p.cm)?'':'<span class="uncrawled">未抓</span>')+'</span>'+
      '<span>点赞 <b>'+p.lk+'</b></span>'+
      '<span>收藏 <b>'+p.fv+'</b></span></span>'+
    '</div></article>';
}
function floorHtml(f){
  var cls='cmt'+(f.pid?' sub':'')+(f.own?' own':'');
  return '<div class="'+cls+'">'+avatarHtml(f.a,f.n,'',f.uid)+'<div class="cbody">'+
    '<div class="cnm"><span class="nm clickable" data-uid="'+f.uid+
      '" title="查看 TA 的全部帖子">'+esc(f.n)+'</span>'+
      (f.lv?' <span class="lv">Lv.'+f.lv+'</span>':'')+
      (f.own?'<span class="op">楼主</span>':'')+
      '<span class="tm">'+esc(f.t)+'</span></div>'+
    '<div class="cc">'+(f.r?'<span class="reply-to">回复 '+esc(f.r)+'：</span>':'')+esc(f.c)+'</div>'+
    (f.img?'<img class="one" src="'+esc(f.img)+'" loading="lazy" alt="" data-full="'+esc(fullUrl(f.img))+'"'+
      ' onload="this.setAttribute(\'data-loaded\',\'1\')"'+
      ' onerror="this.setAttribute(\'data-loaded\',\'1\');this.style.visibility=\'hidden\'">':'')+
    '<div class="ins"><span>赞 '+f.lk+'</span>'+(f.d?'<span>踩 '+f.d+'</span>':'')+
      '<button class="act user-only flbtn" onclick="toggleFloorLike('+f.id+',this)">赞</button>'+
      '<button class="act user-only flbtn" data-nm="'+escAttr(f.n||'')+'" '+
        'onclick="replyToFloor('+f.id+',this.getAttribute(\'data-nm\'))">回复</button></div>'+
  '</div></div>';
}
function officialHtml(f){
  return '<div class="cmt official">'+avatarHtml(f.a||'',f.n)+'<div class="cbody">'+
    '<div class="cnm">'+esc(f.n)+'<span class="tm">'+esc(f.t)+'</span></div>'+
    '<div class="cc">'+esc(f.c)+'</div>'+
    (f.img?'<img class="one" src="'+esc(f.img)+'" loading="lazy" alt="" data-full="'+esc(fullUrl(f.img))+'"'+
      ' onload="this.setAttribute(\'data-loaded\',\'1\')"'+
      ' onerror="this.setAttribute(\'data-loaded\',\'1\');this.style.visibility=\'hidden\'">':'')+
    (f.lk?'<div class="ins"><span>赞 '+f.lk+'</span></div>':'')+
  '</div></div>';
}
/* 打开帖子详情（从列表点卡片走这里，会压栈） */
function openPost(id){ navPush('post',id); }

/* 只负责把帖子渲染进弹窗；导航由栈统一管理 */
/* 详情从接口取完整数据：列表接口只返回摘要（正文截断、不含评论），
   完整正文与全部楼层要单独请求。*/
function renderPost(id){
  // 骨架屏：按详情页的真实结构摆位，避免"一句话 → 整页内容"的跳变
  $('#sheet').innerHTML='<div class="sheet">'+
    '<div class="head"><div class="headin">'+
      '<div class="sk" style="width:66px;height:22px"></div>'+
      '<div class="sk" style="width:220px;height:15px"></div>'+
    '</div></div>'+
    '<div class="body">'+
      '<div class="skel" style="padding:0;background:none">'+
        '<div class="sk-row"><div class="sk sk-av"></div>'+
          '<div class="sk sk-nm"></div><div class="sk sk-tm"></div></div>'+
        '<div class="sk sk-ttl" style="width:78%"></div>'+
        '<div class="sk sk-tx"></div><div class="sk sk-tx s2"></div>'+
        '<div class="sk sk-tx"></div><div class="sk sk-tx s3"></div>'+
      '</div></div></div>';
  $('#modal').classList.add('on');
  document.body.style.overflow='hidden';
  busyOn();
  loadPostDetail(id);
}
function loadPostDetail(id){
  api('api/post',{id:id},true).then(function(p){
    if(!p || p.error) throw new Error(p&&p.error?p.error:'接口没有返回这条帖子');
    drawPost(p);
  }).catch(function(e){
    // 以前这里只弹个 toast，用户面前仍是一片空白，只能自己猜怎么重来。
    // 现在直接把失败原因和「重试」放在内容区里。
    $('#sheet').innerHTML='<div class="sheet">'+
      '<div class="head"><div class="headin">'+
        '<button class="back" onclick="navBack()" title="返回列表（Esc）">'+
        '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" '+
        'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '+
        'stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>'+
        '<span>返回</span></button>'+
        '<span class="ttl">#'+id+'</span></div></div>'+
      '<div class="body"><div class="failbox">'+
        '<div class="fmsg">这条帖子没能加载出来</div>'+
        '<div class="fsub">'+esc(e&&e.message?e.message:'未知错误')+'</div>'+
        '<button class="fretry" onclick="loadPostDetail('+id+')">重试</button>'+
      '</div></div></div>';
  }).then(function(){ busyOff() });
}

function drawPost(p){
  rememberPost(p);          // 详情是独立取的，存一份给分享/导出用
  // 浏览器标签页标题跟着帖子走：多开/找历史标签时认得出是哪一篇
  document.title=(p.t||('帖子 #'+p.id))+' · 青年湖底';
  var deeper=NAV.length>1;
  var h='<div class="sheet"><div class="head"><div class="headin">'+
    '<button class="back" onclick="navBack()" title="'+
      (deeper?'返回上一层':'返回列表')+'（Esc 或鼠标侧键）">'+
      '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" '+
      'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '+
      'stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>'+
      '<span>'+(deeper?'返回':'列表')+'</span></button>'+
    '<span class="ttl">'+esc(p.t||('#'+p.id))+'</span>'+
    '</div></div><div class="body">'+
    '<div class="who">'+avatarHtml(p.av,p.a,'',p.uid)+
      '<span class="nm clickable" data-uid="'+p.uid+'" data-mp="#MP'+p.id+
      '" title="点击复制 MP 号">'+
      esc(p.a)+'</span>'+
      (p.lv?'<span class="lv">Lv.'+p.lv+'</span>':'')+
      '<span class="tm">'+esc(p.d)+'</span></div>'+
    (p.c?'<div class="content full">'+esc(p.c)+'</div>':'<div class="content full empty">'+
      (p.gone?'（正文未取到：详情接口返回失败，帖子可能已被删除或隐藏）':'（正文为空）')+'</div>')+
    imgsHtml(p.imgs)+
    '<div class="meta">'+(p.tn?'<span class="tag board" style="background:'+
      typeColor(p.ty)+'">'+esc(p.tn)+'</span>':'')+
      (p.tag?'<span class="tag label">'+esc(p.tag)+'</span>':'')+
      '<span class="counts"><span>浏览 <b>'+p.vs+'</b></span><span>评论 <b>'+p.cm+'</b></span>'+
      '<span>点赞 <b>'+p.lk+'</b></span><span>收藏 <b>'+p.fv+'</b></span></span></div>'+
    '<div class="detail-meta">'+
      actbarHtml(p)+
      // MP 号本身就是分享入口：点一下复制分享口令（与 App 内分享一致）
      '<button class="sharebtn" onclick="sharePost('+p.id+')" '+
        'title="复制分享口令（#MP'+p.id+'）">'+
        '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" '+
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" '+
        'stroke-linejoin="round"><circle cx="18" cy="5" r="3"/>'+
        '<circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>'+
        '<path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4"/></svg>'+
        '<span>分享</span></button>'+
      '<button class="dlbtn" onclick="exportPost('+p.id+')" title="导出为图片">'+
        '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" '+
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" '+
        'stroke-linejoin="round"><path d="M12 3v12"/>'+
        '<path d="M7 10l5 5 5-5"/><path d="M4 21h16"/></svg>'+
        '<span>图片</span></button>'+
      '</div>';
  if(p.offs&&p.offs.length){
    h+='<h3 class="sec">官方回复（'+p.offs.length+'）</h3>'+p.offs.map(officialHtml).join('');
  }
  // 评论框（仅登录用户）：顶层评论走 floor，楼中楼走 floor/reply。
  // 评论由定时抓取入库，发出后不会立刻出现在本地列表里。
  clearReplyTo();
    h+='<div class="replybox user-only" id="replybox-'+p.id+'">'+
      '<div class="reply-target" id="reply-target">'+
        '<span id="reply-target-name"></span>'+
        '<button class="act" onclick="clearReplyTo()">取消</button></div>'+
      '<textarea id="replytext-'+p.id+'" placeholder="写下你的评论…"></textarea>'+
      '<div class="row"><button class="act" onclick="sendReply('+p.id+')">发送评论</button></div></div>'+
    '<div class="cbar">'+
      '<h3 class="sec">评论（'+((p.got||!p.cm)? p.nc : '未抓取')+'）</h3>'+
      '<div class="csort">'+
        '<button class="cbtn on" data-sort="asc">正序</button>'+
        '<button class="cbtn" data-sort="desc">倒序</button>'+
        '<button class="cbtn" data-sort="like">获赞最多</button>'+
        '<button class="cbtn" data-sort="onlyop">只看楼主</button>'+
      '</div></div>';
  // 列表先空着：楼层必须按父子分组再画。
  // 接口 ORDER BY 父楼ID,id 会把顶层楼和楼中楼拆成两段直出，
  // 直接 map(floorHtml) 会让回复堆在所有评论后面、对不上号。
  h+='<div class="clist" id="clist">'+
    (p.nc? '' :
      '<div class="empty" style="padding:20px 0">'+
      (p.cm>0
        ? ('原帖有 '+p.cm+' 条评论，还没同步到看板。')
        : '这个帖子没有评论。')+'</div>')+'</div>';
  if(p.floorsTotal && p.floorsLoaded < p.floorsTotal){
    h+='<button class="loadmore floors-more" id="morefloors" '+
      'onclick="loadMoreFloors('+p.id+')">加载更多评论（已显示 '+
      p.floorsLoaded+' / '+p.floorsTotal+'）</button>';
  }
  h+='</div></div>';
  $('#sheet').innerHTML=h;
  $('#modal').classList.add('on');
  document.body.style.overflow='hidden';
  bindCommentSort(p);
  if(p.nc) drawComments(p, 'asc');
  settleImages($('#sheet'));
}

/* 追加下一页评论。接口按 foff/flim 分页，避免热帖（实测最多 3578 楼）
   一次把 DOM 和内存一起撑爆。 */
function loadMoreFloors(id){
  var p=POST_CACHE[id]||findPost(id);
  if(!p || !p.floorsTotal) return;
  var btn=$('#morefloors');
  if(btn){ btn.disabled=true; btn.textContent='加载中…'; }
  api('api/post',{id:id,foff:p.floorsLoaded||p.floors.length,flim:200},true)
    .then(function(d){
      if(!d||d.error) throw new Error(d&&d.error?d.error:'加载失败');
      p.floors=(p.floors||[]).concat(d.floors||[]);
      p.floorsLoaded=d.floorsLoaded;
      p.floorsTotal=d.floorsTotal!=null?d.floorsTotal:p.floorsTotal;
      rememberPost(p);
      var mode='asc';
      var on=document.querySelector('.csort .cbtn.on');
      if(on) mode=on.getAttribute('data-sort')||'asc';
      drawComments(p, mode);
      if(p.floorsLoaded < p.floorsTotal){
        var b=document.createElement('button');
        b.className='loadmore floors-more';
        b.id='morefloors';
        b.onclick=function(){ loadMoreFloors(id) };
        b.textContent='加载更多评论（已显示 '+p.floorsLoaded+' / '+p.floorsTotal+'）';
        var list=$('#clist');
        if(list && list.parentNode) list.parentNode.insertBefore(b, list.nextSibling);
      }else{
        var old=$('#morefloors');
        if(old && old.parentNode) old.parentNode.removeChild(old);
      }
    })
    .catch(function(e){
      if(btn){ btn.disabled=false;
        btn.textContent='加载失败，点击重试'; }
      toast(e&&e.message?e.message:'评论加载失败');
    });
}

/* 导出/完整排序前把剩余楼层拉齐。热帖可能还有几百楼没加载。 */
async function ensureAllFloors(p){
  if(!p) return p;
  if(!p.floorsTotal) p.floorsTotal=p.nc||0;
  if(!p.floorsLoaded) p.floorsLoaded=(p.floors||[]).length;
  while(p.floorsLoaded < p.floorsTotal){
    var d=await api('api/post',{id:p.id,foff:p.floorsLoaded,flim:500},true);
    if(!d||!d.floors||!d.floors.length) break;
    p.floors=(p.floors||[]).concat(d.floors);
    p.floorsLoaded=d.floorsLoaded;
    if(d.floorsTotal!=null) p.floorsTotal=d.floorsTotal;
  }
  rememberPost(p);
  return p;
}

/* 评论排序：
   - 正序 / 倒序：按回复时间（顶层楼排序，楼中楼跟着父楼走）
   - 获赞最多：按本楼点赞数降序（同样保持父子关系）
   - 只看楼主：过滤出楼主自己的楼层（含其楼中楼）
   楼中楼必须始终紧跟父楼，否则层级会乱，所以排序只作用于顶层楼。*/
function bindCommentSort(p){
  var bar=document.querySelector('.csort');
  if(!bar) return;
  bar.querySelectorAll('.cbtn').forEach(function(btn){
    btn.addEventListener('click',function(){
      bar.querySelectorAll('.cbtn').forEach(function(x){x.classList.remove('on')});
      btn.classList.add('on');
      var mode=btn.getAttribute('data-sort');
      // 还有楼层没加载时先拉齐，否则排序只作用于当前这批
      if(p.floorsTotal && (p.floors||[]).length < p.floorsTotal){
        var old=btn.textContent;
        btn.textContent='加载中…';
        ensureAllFloors(p).then(function(){
          btn.textContent=old;
          drawComments(p, mode);
        }).catch(function(){
          btn.textContent=old;
          drawComments(p, mode);
        });
      }else{
        drawComments(p, mode);
      }
    });
  });
}

/* 楼层分组 + 顶层排序。
   接口按「父楼ID,id」分页返回：顶层楼（pid=0）全在前，楼中楼按父楼堆在后。
   展示/导出都必须先这样归位，否则回复会和父评论对不上号。*/
function groupFloors(p, mode){
  var tops=[], subs={};
  (p.floors||[]).forEach(function(f){
    if(f.pid>0){ (subs[f.pid]=subs[f.pid]||[]).push(f) }
    else tops.push(f);
  });
  Object.keys(subs).forEach(function(k){
    subs[k].sort(function(a,b){ return (a.t||'').localeCompare(b.t||'') });
  });

  if(mode==='onlyop'){
    tops=tops.filter(function(f){ return f.own });
  }else if(mode==='like'){
    tops.sort(function(a,b){ return (b.lk||0)-(a.lk||0) });
  }else if(mode==='desc'){
    tops.sort(function(a,b){ return (b.t||'').localeCompare(a.t||'') });
  }else{                                // asc 默认
    tops.sort(function(a,b){ return (a.t||'').localeCompare(b.t||'') });
  }
  return {tops:tops, subs:subs};
}

/* 分组结果展平成渲染/导出用的一维列表（父楼后紧跟其楼中楼） */
function flattenGrouped(g){
  var out=[];
  g.tops.forEach(function(f){
    out.push(f);
    (g.subs[f.id]||[]).forEach(function(c){ out.push(c) });
  });
  return out;
}

function drawComments(p, mode){
  var list=$('#clist');
  if(!list) return;
  if(!p.nc) return;                     // 没评论无需重绘

  var g=groupFloors(p, mode);
  var html='';
  g.tops.forEach(function(f){
    html+=floorHtml(f);
    (g.subs[f.id]||[]).forEach(function(c){ html+=floorHtml(c) });
  });
  if(!html){
    html='<div class="empty" style="padding:20px 0">'+
         (mode==='onlyop'?'楼主没有在本帖下评论。':'没有可显示的评论。')+'</div>';
  }
  list.innerHTML=html;
  settleImages(list);
}
// 仅关闭弹窗本身（不碰栈）；外部要关闭请用 navClose()
function closePost(){
  $('#modal').classList.remove('on');document.body.style.overflow='';
  document.title=BASE_TITLE;
}

/* ============================================================
   关注：存在浏览器 localStorage 里，刷新不丢。
   只在本地生效，换浏览器/清除数据会丢失（想要跨设备需要存数据库）。
   ============================================================ */
var FOLLOW_KEY='wpy_follow_v1';
var LOCALFAV_KEY='wpy_localfav_v1';
var LOCALFAV={};          // 本地收藏（游客模式）：id -> 1
function loadLocalFav(){
  try{ LOCALFAV=JSON.parse(localStorage.getItem(LOCALFAV_KEY)||'{}')||{} }
  catch(e){ LOCALFAV={} }
}
function saveLocalFav(){
  try{ localStorage.setItem(LOCALFAV_KEY, JSON.stringify(LOCALFAV)) }catch(e){}
}
var USER_NAMES={};        // uid -> 昵称，用于关注栏显示
var FOLLOW={};            // uid -> 昵称

function loadFollow(){
  // 登录用户：服务端持久化（跨设备）；游客：只存本机浏览器
  if(ROLE==='user'){
    return fetch('api/follow').then(function(r){ return r.json() }).then(function(d){
      if(d && d.follows){
        FOLLOW=d.follows;
      }
      renderFollowBar();
    }).catch(function(){ renderFollowBar(); });
  } else {
    try{ FOLLOW=JSON.parse(localStorage.getItem(FOLLOW_KEY)||'{}')||{} }
    catch(e){ FOLLOW={} }
    renderFollowBar();
  }
}
function saveFollow(){
  try{ localStorage.setItem(FOLLOW_KEY, JSON.stringify(FOLLOW)) }catch(e){}
  renderFollowBar();
}
function isFollowed(uid){ return !!FOLLOW[String(uid)] }
function followCount(){ return Object.keys(FOLLOW).length }
function toggleFollow(uid,name){
  uid=String(uid);
  if(ROLE!=='user'){
    if(FOLLOW[uid]){ delete FOLLOW[uid]; toast('已取消关注（仅本机） '+(name||'')) }
    else{ FOLLOW[uid]=name||('用户 '+uid); toast('已关注（仅保存在本机） '+(FOLLOW[uid])) }
    saveFollow();
    return;
  }
  busyOn();
  postForm('api/follow',{uid:uid,name:name||''}).then(function(d){
    if(d && d.ok){
      FOLLOW=d.follows||{};
      toast(FOLLOW[uid]!==undefined ? ('已关注 '+(FOLLOW[uid]||''))
                                    : ('已取消关注 '+(name||'')));
      renderFollowBar();
    } else toast((d && d.error) || '关注失败');
  }).catch(function(){ toast('关注失败：服务不可达') }).then(function(){ busyOff() });
}
// 收集所有出现过的用户名（用于关注栏显示头像）
/* 收集用户信息（uid -> 昵称/头像）。
   注意：服务版里 POSTS 只有「当前页」，靠它收集不全。
   所以这里只做「就地登记」，真正缺的由 ensureUsers() 向后端批量补齐。*/
function collectNames(){
  POSTS.forEach(function(p){
    if(p.uid && (!USER_NAMES[p.uid] || !USER_NAMES[p.uid].av)){
      USER_NAMES[p.uid]={n:p.a,av:p.av};
    }
  });
}

/* 向后端批量补齐指定 uid 的用户信息（关注列表、用户卡片用）。
   带缓存：同一个 uid 只请求一次。*/
var _USER_FETCHED=Object.create(null);
function ensureUsers(uids){
  var need=uids.filter(function(u){ return u && !_USER_FETCHED[u] });
  if(!need.length) return Promise.resolve();
  need.forEach(function(u){ _USER_FETCHED[u]=1 });
  return api('api/users', {uids:need.join(',')}).then(function(d){
    Object.keys(d||{}).forEach(function(k){
      var v=d[k];
      // done 标记「已经查过」：被封禁/异常账号可能 av 为空，
      // 不能再用 av 是否为空判断要不要重查（否则 showUserCard 死循环）。
      USER_NAMES[k]={n:v.name||'', av:v.av||'', lv:v.lv||0,
                     posts:v.posts||0, done:1};
    });
    // 接口没返回的 uid 也标 done，避免反复请求
    need.forEach(function(u){
      if(!USER_NAMES[u]) USER_NAMES[u]={n:FOLLOW[u]||('用户 '+u), av:'', done:1};
      else if(!USER_NAMES[u].done) USER_NAMES[u].done=1;
    });
    renderFollowBar();
  }).catch(function(){
    // 失败就允许下次重试
    need.forEach(function(u){ delete _USER_FETCHED[u] });
  });
}
/* 关注开关：悬停时在下方显示已关注列表。
   浮层挂在 body 下用 fixed 定位（否则会被帖子卡片盖住），
   因此位置必须由 JS 按开关的实际坐标计算。*/
function bindFollowHover(){
  // 触屏没有 hover：浮层改由点开关弹出（见 sw-follow 的点击绑定）
  if(IS_TOUCH) return;
  var wrap=document.getElementById('sw-follow');
  var pop=$('#followpop');
  if(!wrap||!pop) return;
  var hideTimer=null;

  function place(){
    var r=wrap.getBoundingClientRect();
    pop.style.left=Math.max(8,Math.min(r.left, window.innerWidth-pop.offsetWidth-8))+'px';
    pop.style.top=(r.bottom+7)+'px';
  }
  function show(){
    clearTimeout(hideTimer);
    pop.classList.add('on');
    place();                       // 先显示才能量到宽度，再定位
    requestAnimationFrame(place);
  }
  function hide(delay){
    clearTimeout(hideTimer);
    hideTimer=setTimeout(function(){
      if(pop.matches(':hover')||wrap.matches(':hover')) return;
      pop.classList.remove('on');
    }, delay||140);
  }
  wrap.addEventListener('mouseenter',show);
  wrap.addEventListener('mouseleave',function(){hide(140)});
  pop.addEventListener('mouseenter',function(){clearTimeout(hideTimer)});
  pop.addEventListener('mouseleave',function(){hide(120)});
  window.addEventListener('scroll',function(){ if(pop.classList.contains('on')) hide(0) },true);
  window.addEventListener('resize',function(){ if(pop.classList.contains('on')) place() });
}

function renderFollowBar(){
  var pop=$('#followpop');
  if(!pop) return;
  var ids=Object.keys(FOLLOW);
  // 开关旁边的数量提示
  var sw=$('#sw-follow');
  if(sw) sw.innerHTML='<i></i>关注'+(ids.length?' '+ids.length:'');
  if(!ids.length){
    pop.innerHTML='<div class="fempty">还没有关注的人</div>';
    return;
  }
  var h='';
  // 触屏没有 hover，浮层只能靠点开关弹出：
  // 首行放一个「看全部/关闭筛选」的快捷操作，与人员列表用分隔线隔开
  if(IS_TOUCH){
    var on=swOn('follow');
    h+='<div class="fitem touch-act" data-a="'+(on?'off':'on')+'">'+
       '<span class="fname" style="color:var(--primary)">'+
       (on?'关闭关注筛选':'看全部关注动态')+'</span></div>';
  }
  ids.forEach(function(uid){
    var info=USER_NAMES[uid]||{};
    var nm=FOLLOW[uid]||info.n||('用户 '+uid);
    // 竖排列表：头像 + 昵称
    h+='<div class="fitem" data-uid="'+uid+'" data-name="'+esc(nm)+'">'+
       avatarHtml(info.av||'',nm)+
       '<span class="fname">'+esc(nm)+'</span></div>';
  });
  pop.innerHTML=h;
  // 缺头像的向后端批量补（本地只有当前页的数据，补不到就会显示字母占位）
  ensureUsers(ids.map(Number));
  var act=pop.querySelector('.fitem.touch-act');
  if(act) act.addEventListener('click',function(e){
    e.stopPropagation();
    pop.classList.remove('on');
    setSw('follow', act.getAttribute('data-a')==='on');
    applyFilter();
  });
  pop.querySelectorAll('.fitem').forEach(function(it){
    var uid=Number(it.getAttribute('data-uid'));
    if(!uid) return;                      // 触屏首行操作项没有 data-uid
    var nm=it.getAttribute('data-name');
    // 左键：筛选该用户；右键：取消关注（避免列表里塞按钮）
    it.addEventListener('click',function(e){
      e.stopPropagation();
      pop.classList.remove('on');
      // 来源=关注列表：纯筛选，不显示横幅、退出也不回原帖
      filterByUser(uid,nm,'follow');
    });
    it.addEventListener('contextmenu',function(e){
      e.preventDefault(); e.stopPropagation();
      toggleFollow(uid,nm);
      toast('已取消关注 '+nm);
    });
  });
}

/* 按用户筛选：设置状态并刷新列表（不再打开个人主页弹窗） */
function clearUser(){
  var backTo = CUR_USER ? CUR_USER.from : null;
  var backScroll = CUR_USER ? (CUR_USER.scroll||0) : 0;
  CUR_USER=null;
  applyFilter();
  // 回到原来那条帖子，并还原到点击头像之前的位置。
  // 注意不能滚到「评论区顶部」——那会丢掉正在看的那一层楼。
  if(backTo){
    navPush('post', backTo);
    setTimeout(function(){
      var mEl=$('#modal');
      if(mEl) mEl.scrollTop=backScroll;
    },80);
  }
}

/* origin: 从哪里发起的筛选
     'follow' —— 从关注列表点的人（纯筛选：不显示横幅、退出不回原帖）
     其他      —— 从帖子里的头像/昵称点的人（来自评论区：显示横幅，退出可返回）*/
function filterByUser(uid,name,origin){
  var uidN=Number(uid);
  var fromPost=null;
  var top=NAV.length?NAV[NAV.length-1]:null;
  if(top && top.kind==='post') fromPost=top.id;
  var needBar=(origin!=='follow') && !!fromPost;
  // 记下点击那一刻的滚动位置：退出筛选时要精确回到这里，
  // 而不是简单地跳到评论区顶部（那样会丢掉正在看的那一层楼）。
  var sc=0;
  var mEl=$('#modal');
  if(mEl) sc=mEl.scrollTop;
  CUR_USER={uid:uidN,name:name||'',from:fromPost,bar:needBar,scroll:sc};
  applyFilter();
  // 很多用户只评论、没发过帖（本地数据里这类人有五千多），
  // 点他们的头像会筛出 0 条，页面毫无变化，看起来像"点了没反应"。
  // 这里明确告知，并给出下一步（看 TA 评论过的帖子在哪些帖里）。
  // 注意要等这次筛选真正返回后再判断，否则读到的还是上一次的结果。
  Promise.resolve(_lastLoad).then(function(){
    if(VIEW.length) return;
    // 该用户在多少帖里评论过，按全库口径问后端
    api('api/user',{uid:uidN}).then(function(u){
      if(!u || CUR_USER && CUR_USER.uid!==uidN) return;
      toast(u.commentedOn
        ? (name||'该用户')+' 没有发过帖；TA 在 '+u.commentedOn+' 个帖子里评论过'
        : (name||'该用户')+' 在本库中没有帖子');
    }).catch(function(){
      toast((name||'该用户')+' 在本库中没有帖子');
    });
  });
}

// 复制文本。file:// 页面里 navigator.clipboard 可能不可用，
// 因此回退到旧式的 execCommand，确保双击打开也能复制。
function copyText(t){
  return new Promise(function(res){
    if(navigator.clipboard && window.isSecureContext){
      navigator.clipboard.writeText(t).then(function(){res(true)},
                                            function(){res(fallback(t))});
    }else{
      res(fallback(t));
    }
  });
  function fallback(t){
    try{
      var ta=document.createElement('textarea');
      ta.value=t; ta.style.position='fixed'; ta.style.opacity='0';
      document.body.appendChild(ta); ta.select();
      var ok=document.execCommand('copy');
      ta.remove(); return ok;
    }catch(e){ return false }
  }
}

/* 悬停头像 -> 用户小卡片（含头像/昵称/MPID/统计/关注）。*/
function bindMpHover(){
  // ---- 头像悬停 -> 用户小卡片 ----
  // 触屏没有 hover：卡片改由点头像弹出（见全局 click 里的 [data-uid] 分支）
  if(IS_TOUCH) return;
  // 延迟显示，避免鼠标划过时闪个不停
  var hoverTimer=null;
  document.addEventListener('mouseover',function(e){
    // 卡片内部移动时不重置
    if(e.target.closest('#userpop')) return;
    // 只用头像触发用户卡片；昵称走下面的 MPID 小标签
    var who=e.target.closest('.avatar-link');
    if(!who) return;
    clearTimeout(hoverTimer);
    hoverTimer=setTimeout(function(){
      showUserCard(who.getAttribute('data-uid'), who);
    },160);
  });
  document.addEventListener('mouseout',function(e){
    var who=e.target.closest('.avatar-link');
    if(!who) return;
    if(who.contains(e.relatedTarget)) return;
    clearTimeout(hoverTimer);
    // 移向卡片本身时不关闭，否则按钮点不到
    if(e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('#userpop')) return;
    setTimeout(function(){
      var up=$('#userpop');
      if(up && up.matches(':hover')) return;
      hideUserCard();
    },120);
  });
  var upEl=$('#userpop');
  if(upEl) upEl.addEventListener('mouseleave',function(){ hideUserCard() });

  // 注：这里曾有一段「悬停昵称弹出 MPID 小标签」的逻辑，
  // 后来改为「悬停头像弹出用户卡片」（卡片里已含 MPID），昵称那层就成了重复，
  // 而且它的 mouseout 在部分情况下不触发，浮层会一直卡在屏幕上不消失。
  // 属于遗留残留，已移除。
}

function toast(msg){
  var t=$('#toast'); t.textContent=msg; t.classList.add('on');
  clearTimeout(t._h); t._h=setTimeout(function(){t.classList.remove('on')},3200);
}

/* 把一条帖子导出成 PNG 图片。
   做法：把弹窗内容克隆到一个离屏容器，用 canvas 手绘文本布局，
   不依赖 html2canvas（它需要联网加载，且跨域图片会污染画布）。
   图片单独异步加载：能加载的绘制，加载不了的标注「图片未能载入」，
   保证任何情况下都能导出成功，不会因一张图挂掉整张图。*/
/* 导出前先问是否带评论：评论可能很多，全画进去图会很长，
   因此让用户自己选。选择结果记在 localStorage，下次沿用。*/
function askExportMode(p){
  return new Promise(function(res){
    var hasC = p.nc>0;
    var last = '0';
    try{ last = localStorage.getItem('wpy_exp_cmt')||'0' }catch(e){}
    var box=$('#askbox');
    box.innerHTML=
      '<div class="askmate">'+
        '<div class="askttl">导出为图片</div>'+
        '<div class="asksub">'+(hasC
          ? '这条帖子本地有 '+p.nc+' 条评论，是否一起画进去？'
          : '这条帖子没有本地评论')+'</div>'+
        '<div class="askopts">'+
          '<button class="askbtn'+(last==='0'||!hasC?' on':'')+'" data-v="0">只要正文</button>'+
          '<button class="askbtn'+(last==='1'&&hasC?' on':'')+'" data-v="1"'+
            (hasC?'':' disabled')+'>含 '+p.nc+' 条评论</button>'+
        '</div>'+
        '<div class="askrow">'+
          '<button class="askgo">开始导出</button>'+
          '<button class="askcancel">取消</button>'+
        '</div>'+
      '</div>';
    box.classList.add('on');
    var val = (last==='1'&&hasC)?'1':'0';
    box.querySelectorAll('.askbtn').forEach(function(b){
      b.addEventListener('click',function(){
        val=b.getAttribute('data-v');
        box.querySelectorAll('.askbtn').forEach(function(x){x.classList.remove('on')});
        b.classList.add('on');
      });
    });
    function done(v){ box.classList.remove('on'); res(v) }
    box.querySelector('.askgo').addEventListener('click',function(){
      try{ localStorage.setItem('wpy_exp_cmt',val) }catch(e){}
      done(val==='1');
    });
    box.querySelector('.askcancel').addEventListener('click',function(){ done(null) });
  });
}

async function exportPost(id){
  var p=findPost(id);
  if(!p){ toast('帖子还没加载完，稍后再试'); return }
  if(_EXPORTING){ toast('正在导出，请稍候…'); return }   // 防重复点击
  // 评论可能还没拉齐（详情默认只取前 150 楼）
  if(p.nc && p.floors && p.floors.length < p.nc){
    busyOn();
    try{ await ensureAllFloors(p) }catch(e){}
    busyOff();
  }
  var withCmt=await askExportMode(p);
  if(withCmt===null) return;              // 用户取消
  // 导出前先按父子归位：接口顺序会把楼中楼甩到所有顶层楼后面
  if(withCmt && p.floors && p.floors.length){
    p.floors=flattenGrouped(groupFloors(p, 'asc'));
    rememberPost(p);
  }
  _EXPORTING=true;
  var btn=document.querySelector('.dlbtn');
  var oldTxt=btn?btn.innerHTML:'';
  if(btn){ btn.disabled=true;
    btn.innerHTML='<span>导出中…</span>' }
  busyOn();
  try{
    // 评论按批切分：一批一张图。
    // 为什么要分批：浏览器对 canvas 单边有硬上限（Chrome 约 32767px），
    // 评论多的帖子（实测最多 3578 楼）整张画出来会得到几十万像素高的画布，
    // 超限后 toDataURL 会失败或让页面卡死。分批后每张都不超限，也不丢内容。
    var batches = withCmt ? splitBatches(p) : [null];
    for(var bi=0; bi<batches.length; bi++){
      // 多图时把进度写在按钮上：导出大帖要几十秒，
      // 只有一个静止的"导出中…"会让人以为卡死了。
      if(btn && batches.length>1){
        btn.innerHTML='<span>导出 '+((bi+1))+'/'+batches.length+'…</span>';
      }
      var sub = batches[bi];
      await drawAndSave(p, sub, bi, batches.length);
      if(batches.length>1) await new Promise(function(r){ setTimeout(r,300) });
    }
    toast(batches.length>1
      ? ('已导出 '+batches.length+' 张图片')
      : '已导出图片');
  }catch(e){
    toast('导出失败：'+(e&&e.message?e.message:e));
  }finally{
    _EXPORTING=false;
    busyOff();
    if(btn){ btn.disabled=false; btn.innerHTML=oldTxt }
  }
}

/* 把评论按「一张图能放下的高度」分批。
   每批都重新估算行高，保证不同长度的评论都能合理分组。
   返回的每项是 {floors:[...], part:n, total:m}；无评论时返回 [null]。*/
function splitBatches(p){
  if(!p.nc || !p.floors.length) return [null];
  var W=760, PAD=28, DPR=2;
  var MAXH=15000;                 // 单张图的高度上限（未乘 DPR）
  var cv=document.createElement('canvas');
  var ctx=cv.getContext('2d');
  var F='"PingFang SC","Microsoft YaHei",system-ui,sans-serif';
  // 头部（作者/标题/正文/图片/统计）也要占高度，先粗算
  ctx.font='600 20px '+F;
  var tl=wrap(ctx,p.t||'',W-PAD*2).length;
  ctx.font='14.5px '+F;
  var bl=p.c?wrap(ctx,p.c,W-PAD*2).length:0;
  var imgCells=Math.min((p.imgs||[]).length,9);
  var imgH=imgCells? (imgCells===1? 380 : Math.ceil(imgCells/2)*190) : 0;
  var headH=PAD+44+tl*28+8+bl*24+(imgH?imgH+12:0)+30+PAD;
  // 第一张图放不下评论就单独放正文，评论全部放后续张
  var budget=MAXH-headH-40;

  var batches=[], cur=[], used=0;
  p.floors.forEach(function(f){
    var x0=f.pid>0?PAD+26:PAD, maxW=W-x0-PAD;
    ctx.font='13px '+F;
    var n;
    if(f.r){
      var rw=ctx.measureText('回复 '+f.r+'：').width;
      var f1=wrap(ctx,f.c||'',maxW-rw)[0]||'';
      var rest=(f.c||'').slice(f1.length);
      n=1+(rest?wrap(ctx,rest,maxW).length:0);
    }else{
      n=wrap(ctx,f.c||'',maxW).length;
    }
    var add=19+n*20+(f.lk?16:0)+10+14;   // +14 给分隔线留空
    if(cur.length && used+add>budget){
      batches.push(cur); cur=[]; used=0;
    }
    cur.push(f); used+=add;
  });
  if(cur.length) batches.push(cur);
  if(!batches.length) return [null];
  return batches.map(function(b,i){ return {floors:b, part:i+1, total:batches.length} });
}

/* 画一张图并保存。sub 为 null 表示不含评论。*/
async function drawAndSave(p, sub, bi, total){
  var DPR=2, W=760, PAD=28;
  var cv=document.createElement('canvas');
  var ctx=cv.getContext('2d');
  var F='"PingFang SC","Microsoft YaHei",system-ui,sans-serif';
  var first = bi===0;

  // 预载图片（只有第一张画正文图片）；用 origin，thumb 导出会糊
  var imgs=[];
  if(first){
    for(var i=0;i<Math.min((p.imgs||[]).length,9);i++){
      imgs.push(await loadImg(fullUrl(p.imgs[i])));
    }
  }

  ctx.font='600 20px '+F;
  var titleLines=wrap(ctx,p.t||('#'+p.id),W-PAD*2);
  ctx.font='14.5px '+F;
  var bodyLines=(first&&p.c)?wrap(ctx,p.c,W-PAD*2):[];
  var metaH=first?26:0;

  // 图片排布
  var imgRows=[], imgH=0;
  if(imgs.length){
    var cols=imgs.length===1?1:(imgs.length<=4?2:3);
    var cellW=(W-PAD*2-(cols-1)*6)/cols;
    for(var r=0;r<Math.ceil(imgs.length/cols);r++){
      var row=imgs.slice(r*cols,r*cols+cols);
      var hh=cellW*(imgs.length===1?0.66:1);
      imgRows.push({row:row,h:hh,cellW:cellW});
      imgH+=hh+6;
    }
  }

  // 评论高度
  var cmtH=0;
  if(sub){
    ctx.font='13px '+F;
    cmtH=52;                                  // 标题 + 分隔线
    sub.floors.forEach(function(f){
      var x0=f.pid>0?PAD+26:PAD, maxW=W-x0-PAD;
      var n;
      if(f.r){
        var rw=ctx.measureText('回复 '+f.r+'：').width;
        var f1=wrap(ctx,f.c||'',maxW-rw)[0]||'';
        var rest=(f.c||'').slice(f1.length);
        n=1+(rest?wrap(ctx,rest,maxW).length:0);
      }else{
        n=wrap(ctx,f.c||'',maxW).length;
      }
      cmtH+=19+n*20+(f.lk?16:0)+10+14;
    });
  }

  var headBlock = first
    ? PAD+44+titleLines.length*28+8+bodyLines.length*24+(imgH?imgH+12:0)
    : PAD+30;
  var H=headBlock+metaH+cmtH+PAD;
  cv.width=W*DPR; cv.height=H*DPR;
  ctx.scale(DPR,DPR);
  ctx.fillStyle='#fff'; ctx.fillRect(0,0,W,H);
  var y=PAD;

  if(first){
    // 作者行
    ctx.fillStyle='#2A2A2A'; ctx.font='600 15px '+F;
    ctx.fillText(p.a||'匿名',PAD,y+4);
    var ax=PAD+ctx.measureText(p.a||'匿名').width+8;
    if(p.lv){
      ctx.fillStyle='#F5A623'; roundRect(ctx,ax,y-9,42,18,9); ctx.fill();
      ctx.fillStyle='#fff'; ctx.font='700 11px '+F;
      ctx.fillText('Lv.'+p.lv,ax+11,y+4);
    }
    ctx.fillStyle='#979797'; ctx.font='12.5px '+F; ctx.textAlign='right';
    ctx.fillText(p.d||'',W-PAD,y+4); ctx.textAlign='left';
    y+=30;
    ctx.fillStyle='#2A2A2A'; ctx.font='600 20px '+F;
    titleLines.forEach(function(l){ ctx.fillText(l,PAD,y+8); y+=28 });
    if(bodyLines.length){
      ctx.fillStyle='#4E4E4E'; ctx.font='14.5px '+F;
      bodyLines.forEach(function(l){ ctx.fillText(l,PAD,y+4); y+=24 });
    }
    if(imgRows.length){
      y+=6;
      imgRows.forEach(function(row){
        var x=PAD;
        row.row.forEach(function(im){
          var w=row.cellW, h=row.h;
          if(im&&im.complete&&im.naturalWidth){
            var sr=w/h, ir=im.naturalWidth/im.naturalHeight;
            var sw=im.naturalWidth, sh=im.naturalHeight, sx=0, sy=0;
            if(ir>sr){ sw=im.naturalHeight*sr; sx=(im.naturalWidth-sw)/2 }
            else{ sh=im.naturalWidth/sr; sy=(im.naturalHeight-sh)/2 }
            ctx.drawImage(im,sx,sy,sw,sh,x,y,w,h);
          }else{
            ctx.fillStyle='#EEF1F5'; roundRect(ctx,x,y,w,h,6); ctx.fill();
            ctx.fillStyle='#B9C2CF'; ctx.font='12px '+F; ctx.textAlign='center';
            ctx.fillText('图片未能载入',x+w/2,y+h/2+4); ctx.textAlign='left';
          }
          x+=w+6;
        });
        y+=row.h+6;
      });
    }
    y+=4;
    ctx.fillStyle='#2C7EDF'; ctx.font='600 12px '+F;
    var bw=ctx.measureText(p.tn).width+18;
    roundRect(ctx,PAD,y-4,bw,20,6); ctx.fill();
    ctx.fillStyle='#fff'; ctx.fillText(p.tn,PAD+9,y+10);
    ctx.fillStyle='#979797'; ctx.font='12.5px '+F; ctx.textAlign='right';
    ctx.fillText('浏览 '+p.vs+'   评论 '+p.cm+'   点赞 '+p.lk+'   收藏 '+p.fv,
                 W-PAD,y+10);
    ctx.textAlign='left';
  }else{
    ctx.fillStyle='#2A2A2A'; ctx.font='600 15px '+F;
    ctx.fillText((p.t||('#'+p.id)),PAD,y+4);
    y+=30;
  }

  // 评论
  if(sub){
    y+=14;
    var label='评论（'+p.nc+'）';
    if(total>1) label+=' · 第 '+sub.part+'/'+total+' 张';
    ctx.fillStyle='#979797'; ctx.font='600 12.5px '+F;
    ctx.fillText(label,PAD,y);
    y+=8;
    ctx.strokeStyle='#EDF1F7'; ctx.beginPath();
    ctx.moveTo(PAD,y); ctx.lineTo(W-PAD,y); ctx.stroke();
    y+=16;
    sub.floors.forEach(function(f){
      var isSub=f.pid>0;
      var x0=isSub?PAD+26:PAD;
      var maxW=W-x0-PAD;
      if(isSub){
        ctx.fillStyle='#E9EEF5'; roundRect(ctx,x0-14,y-2,3,18,2); ctx.fill();
      }
      ctx.fillStyle='#2A2A2A'; ctx.font='600 12.5px '+F;
      var nw=ctx.measureText(f.n).width;
      ctx.fillText(f.n,x0,y);
      if(f.lv){
        ctx.fillStyle='#F5A623'; roundRect(ctx,x0+nw+6,y-10,34,14,7); ctx.fill();
        ctx.fillStyle='#fff'; ctx.font='700 9.5px '+F;
        ctx.fillText('Lv.'+f.lv,x0+nw+12,y+1);
      }
      ctx.fillStyle='#B9C2CF'; ctx.font='11px '+F; ctx.textAlign='right';
      ctx.fillText(f.t||'',W-PAD,y); ctx.textAlign='left';
      y+=19;
      ctx.font='13px '+F;
      if(f.r){
        var prefix='回复 '+f.r+'：';
        var rw2=ctx.measureText(prefix).width;
        var fl=wrap(ctx,f.c||'',maxW-rw2)[0]||'';
        ctx.fillStyle='#2C7EDF'; ctx.font='600 13px '+F;
        ctx.fillText(prefix,x0,y);
        ctx.fillStyle='#4E4E4E'; ctx.font='13px '+F;
        if(fl) ctx.fillText(fl,x0+rw2,y);
        var rest2=(f.c||'').slice(fl.length);
        y+=20;
        if(rest2) wrap(ctx,rest2,maxW).forEach(function(l){ ctx.fillText(l,x0,y); y+=20 });
      }else{
        wrap(ctx,f.c||'',maxW).forEach(function(l){ ctx.fillText(l,x0,y); y+=20 });
      }
      if(f.lk){
        ctx.fillStyle='#C9D2DE'; ctx.font='11px '+F;
        ctx.fillText('赞 '+f.lk,x0,y); y+=16;
      }
      // 楼层分隔线
      y+=4;
      ctx.strokeStyle='#F4F6FA'; ctx.beginPath();
      ctx.moveTo(x0,y); ctx.lineTo(W-PAD,y); ctx.stroke();
      y+=10;
    });
  }

  // 水印
  y+=6;
  ctx.fillStyle='#C9D2DE'; ctx.font='11.5px '+F;
  var wm='青年湖底 · 帖子 '+p.id+(total>1?(' · '+(bi+1)+'/'+total):'');
  ctx.fillText(wm,W-PAD-ctx.measureText(wm).width,y);

  var url=cv.toDataURL('image/png');
  var base='微北洋_'+p.id+'_'+(p.t||'帖子').replace(/[\\/:*?"<>|]/g,'_').slice(0,26);
  var a=document.createElement('a');
  a.download=base+(total>1?('_第'+(bi+1)+'张'):'')+'.png';
  a.href=url; a.click();
}
function loadImg(src){
  return new Promise(function(res){
    if(!src) return res(null);
    var im=new Image();
    im.crossOrigin='anonymous';        // 允许读到像素，避免污染画布
    var done=false;
    var fin=function(v){ if(!done){done=true;res(v)} };
    im.onload=function(){fin(im)}; im.onerror=function(){fin(null)};
    setTimeout(function(){fin(im.complete&&im.naturalWidth?im:null)},5000);
    im.src=src;
  });
}
function wrap(ctx,text,maxW){
  var out=[];
  String(text).split('\n').forEach(function(para){
    if(!para){ out.push(''); return }
    var line='';
    for(var i=0;i<para.length;i++){
      var t=line+para[i];
      if(ctx.measureText(t).width>maxW && line){ out.push(line); line=para[i] }
      else line=t;
      if(out.length>60) break;         // 超长正文截断，避免图片过高
    }
    if(line) out.push(line);
  });
  if(out.length>60){ out=out.slice(0,60); out.push('……（正文过长已截断）') }
  return out;
}
function roundRect(ctx,x,y,w,h,r){
  ctx.beginPath();
  ctx.moveTo(x+r,y); ctx.arcTo(x+w,y,x+w,y+h,r); ctx.arcTo(x+w,y+h,x,y+h,r);
  ctx.arcTo(x,y+h,x,y,r); ctx.arcTo(x,y,x+w,y,r); ctx.closePath();
}
/* 重新查询（筛选条件变了就调它） */


/* 年份下拉：从接口取实际有数据的年份（含每一年帖子数） */
function initYears(){
  api('api/init').then(function(d){
    var sel=$('#yr');
    if(!sel) return;
    (d.years||[]).forEach(function(x){
      sel.insertAdjacentHTML('beforeend',
        '<option value="'+x.y+'">'+x.y+' 年（'+x.n.toLocaleString()+'）</option>');
    });
  }).catch(function(){});
}

/* 筛选条件变化时重新查询（清空列表从头拉） */
function applyFilter(){
  PAGE=0;
  return (_lastLoad=loadPage(true));
}

/* 拉取一页数据并渲染。
   reset=true 清空重来；false 追加下一页。
   数据来自本地接口，因此 20 万帖也能秒开、能翻到任意年份。

   加载观感分两种情况：
     - 首次进入（列表还是空的）：显示骨架屏，明确"内容正在来"
     - 切换筛选（列表已有内容）：保留旧内容并降透明度，
       新结果就绪后原地替换。比"先清空再等"少一次整片白屏。*/
/* 请求在途时又改了筛选：置 _requery，当前请求结束后补跑一次 */
var _requery=false;

function loadPage(reset){
  // 已经有请求在飞：复用它，不要直接返回一个已完成的 Promise，
  // 否则调用方会以为「加载完了」，读到的还是上一次的结果。
  // 在途时又改了筛选：当前请求结束后补跑一次（_requery），
  // 否则这次点击会被 LOADING 守卫吞掉——用户点了「精华」毫无反应，
  // chip 高亮切了、列表却还是旧条件的结果。
  if(LOADING){
    if(reset) _requery=true;
    return _lastLoad || Promise.resolve();
  }
  LOADING=true;
  var list=$('#list');
  var kw=($('#q').value||'').trim();
  SEARCH_TERMS = kw ? kw.split(/\s+/).filter(function(x){return x}) : null;

  var isEmpty = !list.querySelector('.card');
  if(reset){
    if(isEmpty) list.innerHTML=skeletonHtml(4);   // 首次：骨架
    else list.classList.add('busy');              // 切换：旧内容淡着等
  }
  busyOn();

  var q=buildQuery();
  return api('api/posts', q).then(function(d){
    TOTAL=d.total;
    VIEW=d.items; POSTS=d.items;
    // 一次性拼好再插入，避免逐条 append 引起多次重排（长列表时很明显）
    var html=d.items.map(cardHtml).join('');
    list.classList.remove('busy');
    if(reset) list.innerHTML=html;
    else list.insertAdjacentHTML('beforeend',html);
    settleImages(list);          // 给卡住的图片兜底收尾
    PAGE++;
    prefetchNextPage(q);
    var shown=Math.min(PAGE*SZ,TOTAL);
    var more=shown<TOTAL;
    $('#more').style.display=more?'block':'none';
    $('#more').textContent=more
      ? ('加载更多（已显示 '+shown.toLocaleString()+' / '+TOTAL.toLocaleString()+' 条）')
      : ('已全部显示（共 '+TOTAL.toLocaleString()+' 条）');
    $('#empty').style.display=TOTAL?'none':'block';
    syncUI();
  }).catch(function(e){
    list.classList.remove('busy');
    list.innerHTML='<div class="failbox">'+
      '<div class="fmsg">数据加载失败</div>'+
      '<div class="fsub">'+esc(e.message||'')+'<br>'+
      '网络或服务暂时不可用，请稍后重试</div>'+
      '<button class="fretry" onclick="applyFilter()">重试</button></div>';
  }).then(function(){ LOADING=false; busyOff();
    if(_requery){ _requery=false; applyFilter(); }
  });
}

/* 后台预取下一页：
   等用户滚到底再请求会有明显等待感，提前把下一页拉进缓存，
   点「加载更多」时几乎瞬间出结果。失败静默忽略，不影响主流程。*/
function prefetchNextPage(curQ){
  var shown=PAGE*SZ;
  if(shown>=TOTAL) return;                 // 没有下一页
  var q={};
  for(var k in curQ) q[k]=curQ[k];
  q.page=PAGE+1;
  setTimeout(function(){ api('api/posts', q).catch(function(){}) }, 200);
}

/* 把界面上的筛选状态收集成接口参数 */
function buildQuery(){
  var p={page:PAGE+1, size:SZ, tab:CUR_TAB, so:$('#so').value,
         q:($('#q').value||'').trim()};
  if(ROLE==='user' && swOn('mine')) p.mine=1;
  if(swOn('fav')){
    if(ROLE==='user') p.fav=1;
    else p.favIds=Object.keys(LOCALFAV).join(',')||'0';
  }
  if(swOn('unc')) p.unc=1;
  if(swOn('gone')) p.gone=1;
  var d1=$('#d1').value||'', d2=$('#d2').value||'';
  if(d1) p.d1=d1;
  if(d2) p.d2=d2;
  var yr=$('#yr'); if(yr&&yr.value) p.y=yr.value;
  if(CUR_USER) p.uid=CUR_USER.uid;
  if(swOn('follow')) p.followUids=Object.keys(FOLLOW).join(',');
  return p;
}
function num(id){var v=($(id).value||'').trim();return v===''?null:Number(v)}

// 顶栏分区导航。"其他"= 二次元/摆摊/运动健身/数码科技/Bugs反馈
var OTHER_TYPES=[5,6,7,8,10];
var CUR_TAB='all';                       // 当前选中的分区标签
var CUR_USER=null;                       // 当前按用户筛选（{uid,name}）
var SEARCH_SCORE=null;                   // 搜索命中的相关度分数（按帖子 id）
var SEARCH_TERMS=null;                   // 当前搜索关键词，用于高亮

function swOn(k){var e=$('#sw-'+k);return !!(e&&e.classList.contains('on'))}
function setSw(k,on){var e=$('#sw-'+k);if(!e)return;e.classList.toggle('on',!!on)}
function tabMatch(p,tab){
  if(tab==='all') return true;
  if(tab==='ess') return !!p.ess;
  if(tab==='other') return OTHER_TYPES.indexOf(p.ty)>=0;
  return String(p.ty)===String(tab);
}
function syncUI(){
  // 用户筛选提示条
  var ub=$('#userbar');
  if(ub){
    if(CUR_USER && CUR_USER.bar){
      ub.classList.add('on');
      ub.innerHTML='<div class="ub"><span>正在看</span>'+
        '<span class="uz">'+esc(CUR_USER.name||('用户 '+CUR_USER.uid))+'</span>'+
        '<span class="ucount">'+VIEW.length+' 条帖子</span>'+
        '<button class="uout" onclick="clearUser()">退出</button></div>';
    }else{
      ub.classList.remove('on'); ub.innerHTML='';
    }
  }
  document.querySelectorAll('.chip').forEach(function(c){
    c.classList.toggle('on', c.getAttribute('data-tab')===CUR_TAB);
  });
  // 有任一筛选生效时，给「筛选」按钮加上选中态提示
  var any=['mine','fav','follow'].some(swOn) || !!CUR_USER
          || $('#d1').value || $('#d2').value || $('#so').value!=='new';
  $('#btnfilter').classList.toggle('on',!!any);
}
function togglePanel(){$('#panel').classList.toggle('open')}

/* ============================================================
   分区标签拖动排序：只排 .chip，开关（我的/收藏/关注）固定在后。
   顺序存 localStorage，换浏览器/清数据会回到默认。
   ============================================================ */
var TAB_ORDER_KEY='wpy_tab_order_v1';
var TAB_ORDER_DEFAULT=['all','ess','2','3','4','13','1','other'];

function chipsOf(){
  return Array.prototype.slice.call(document.querySelectorAll('.tabbar .chip'));
}
function loadTabOrder(){
  try{
    var arr=JSON.parse(localStorage.getItem(TAB_ORDER_KEY)||'null');
    if(Array.isArray(arr)&&arr.length) return arr;
  }catch(e){}
  return TAB_ORDER_DEFAULT.slice();
}
function saveTabOrder(order){
  try{ localStorage.setItem(TAB_ORDER_KEY, JSON.stringify(order)) }catch(e){}
}
function applyTabOrder(){
  var bar=document.querySelector('.tabbar');
  if(!bar) return;
  var order=loadTabOrder();
  var chips=chipsOf();
  var byId={};
  chips.forEach(function(c){ byId[c.getAttribute('data-tab')]=c });
  // 按保存顺序排列；未知 id 追加在末尾
  var seen={};
  var seq=[];
  order.forEach(function(id){
    if(byId[id]&&!seen[id]){ seq.push(byId[id]); seen[id]=1 }
  });
  chips.forEach(function(c){
    var id=c.getAttribute('data-tab');
    if(!seen[id]){ seq.push(c); seen[id]=1 }
  });
  var divider=bar.querySelector('.divider');
  seq.forEach(function(c){ bar.insertBefore(c, divider||null) });
}
function currentTabOrder(){
  return chipsOf().map(function(c){ return c.getAttribute('data-tab') });
}
function bindTabDrag(){
  var bar=document.querySelector('.tabbar');
  if(!bar) return;
  var dragging=null;
  var startPts={x:0,y:0};

  function reorderTo(pointerX){
    if(!dragging) return;
    var list=chipsOf();
    var target=null;
    for(var i=0;i<list.length;i++){
      var r=list[i].getBoundingClientRect();
      if(pointerX>=r.left && pointerX<=r.right){ target=list[i]; break }
    }
    if(!target && list.length){
      var lastR=list[list.length-1].getBoundingClientRect();
      target = pointerX < lastR.left ? list[0] : list[list.length-1];
    }
    if(!target || target===dragging) return;
    var from=list.indexOf(dragging);
    var to=list.indexOf(target);
    if(from<0 || to<0 || from===to) return;
    // 插入到 target 前/后，完成交换
    if(from<to) bar.insertBefore(dragging, target.nextSibling);
    else bar.insertBefore(dragging, target);
    dragging._dragMoved=true;
  }

  bar.addEventListener('pointerdown', function(e){
    if(e.button!=null && e.button!==0) return;
    var chip=e.target.closest?e.target.closest('.chip'):null;
    if(!chip || !bar.contains(chip)) return;
    e.preventDefault();
    dragging=chip;
    startPts.x=e.clientX; startPts.y=e.clientY;
    chip._dragMoved=false;
    try{ chip.setPointerCapture(e.pointerId) }catch(err){}
    chip.classList.add('dragging');
  });

  bar.addEventListener('pointermove', function(e){
    if(!dragging) return;
    var dx=e.clientX-startPts.x, dy=e.clientY-startPts.y;
    // 横向位移超过 3px 才当作拖动，避免误触
    if(!dragging._dragMoved && Math.abs(dx)<3 && Math.abs(dy)<40) return;
    if(Math.abs(dy)>80 && !dragging._dragMoved){ /* 允许，仍以横向为准 */ }
    reorderTo(e.clientX);
  });

  function endDrag(){
    if(!dragging) return;
    dragging.classList.remove('dragging');
    var moved=!!dragging._dragMoved;
    dragging=null;
    if(moved) saveTabOrder(currentTabOrder());
  }
  bar.addEventListener('pointerup', endDrag);
  bar.addEventListener('pointercancel', endDrag);

  chipsOf().forEach(function(c){ c.title='按住左右拖动可调整顺序' });
}

/* 标签行左右渐隐提示：内容能横向滑动且没滑到头时亮起，滑到头就熄灭。
   手机上「我的/收藏/关注」在滚动区外，滚动条又被隐藏，
   没有这个提示根本发现不了右边还有开关。*/
function bindTabFade(){
  var bar=document.querySelector('.tabbar');
  var wrapEl=document.querySelector('.tabwrap');
  if(!bar||!wrapEl) return;
  function upd(){
    var max=bar.scrollWidth-bar.clientWidth;
    wrapEl.classList.toggle('fade-r', max>4 && bar.scrollLeft<max-4);
    wrapEl.classList.toggle('fade-l', bar.scrollLeft>4);
  }
  bar.addEventListener('scroll',upd,{passive:true});
  window.addEventListener('resize',upd);
  window.addEventListener('load',upd);
  upd();
}

function queryTerms(q){
  var t=[];
  var re=/"([^"]+)"/g, m;
  while((m=re.exec(q))) t.push(m[1].toLowerCase());
  q.replace(re,' ').split(/\s+/).forEach(function(x){
    x=x.trim().toLowerCase(); if(x) t.push(x);
  });
  return t;
}
// 回到主界面：清空搜索、日期、开关、排序，并切回「全部」分区
function goHome(){
  navClose();          // 清空导航栈并关闭弹窗
  clearFilters();
  window.scrollTo({top:0,behavior:'smooth'});
}

function clearFilters(){
  ['#q','#d1','#d2'].forEach(function(s){$(s).value=''});
  var yr=$('#yr'); if(yr) yr.value='';
  ['mine','fav','follow'].forEach(function(k){setSw(k,false)});
  $('#so').value='new';
  CUR_TAB='all';
  CUR_USER=null;
  applyFilter();
}
/* ============================================================
   导航栈：所有弹窗页面（帖子详情、用户主页）统一走这里。
   打开新页面 -> 压栈；点「← 返回」-> 出栈回上一层；栈空则关弹窗。
   这样无论从哪进哪，都能原路退回，不会迷路。
   ============================================================ */
var NAV=[];

// 当前是否处于「从列表直接进主页」的根页面（根页面返回时回列表）
/* 打开新页面时同时往浏览器历史里压一条记录，
   这样鼠标侧键 / 后退键（都触发 popstate）就能原路返回。
   自增标记 _navDepth 用来判断这次 popstate 是自己压的那条还是外部导航。*/
var _navDepth=0;
function navPush(kind,id){
  NAV.push({kind:kind,id:id});
  _navDepth++;
  try{ history.pushState({_nav:_navDepth},'') }catch(e){}
  renderTop();
}
function navBack(){
  // 走历史返回，让侧键与按钮行为一致
  try{ history.back() }catch(e){ _doNavBack() }
}
// 真正执行出栈（由 popstate 或兜底直接调用）
function _doNavBack(){
  NAV.pop();
  _navDepth=Math.max(0,_navDepth-1);
  if(!NAV.length){ closePost(); return }
  renderTop();
}
function navReset(){ NAV=[]; _navDepth=0 }
function navDepth(){ return NAV.length }
// 关闭弹窗（清空栈，并把历史里压的记录一起收回）
function navClose(){
  var n=_navDepth;
  NAV=[]; _navDepth=0;
  closePost();
  // 一次性退回初始状态，避免用户再按后退时还要按好几次
  if(n>0){ try{ history.go(-n) }catch(e){} }
}
// 浏览器后退 / 鼠标侧键
window.addEventListener('popstate',function(){
  if(!NAV.length) return;      // 弹窗没开，交给浏览器默认行为
  _doNavBack();
});

function renderTop(){
  var top=NAV[NAV.length-1];
  setHotOpen(top && top.kind==='post' ? top.id : null);
  if(!top) return;
  if(top.kind==='post') renderPost(top.id);
  var host=$('#sheet');
  host.scrollTop=0;
}


/* 悬停头像时弹出用户小卡片：头像、昵称、MPID、简单统计、关注按钮。
   点卡片里的关注可切换关注；点头像本身即筛选该用户的帖子，
   因此不再重复放「看 TA 的帖子」按钮。
   完整个人主页已移除：它占满屏幕，鼠标一移开就消失，反而不好用。*/
function showUserCard(uid,anchorEl){
  var uidN=Number(uid);
  if(!uidN) return;
  // 用 done 判断是否已拉取过；不能用 av 是否有值——
  // 负等级/被处理账号可能没有头像，av=='' 会陷入无限补全循环。
  if(!USER_NAMES[uidN] || !USER_NAMES[uidN].done){
    ensureUsers([uidN]).then(function(){
      var pop=$('#userpop');
      if(pop && pop.classList.contains('on') && pop._uid===uidN){
        showUserCard(uidN,anchorEl);
      }
    });
  }
  var mine=POSTS.filter(function(p){return p.uid===uidN});
  var info=USER_NAMES[uidN]||{};
  var p0=mine[0]||null;
  var nm=info.n||(p0&&p0.a)||'';
  var av=info.av||(p0&&p0.av)||'';
  // 兜底：只评论过的用户不在 POSTS 里，直接从楼层找一次
  if(!nm||!av){
    outer:
    for(var i=0;i<POSTS.length;i++){
      var fl=POSTS[i].floors;
      if(!fl) continue;
      for(var k=0;k<fl.length;k++){
        if(fl[k].uid===uidN){
          nm=nm||fl[k].n||'';
          av=av||fl[k].a||'';
          break outer;
        }
      }
    }
  }
  if(!nm) nm='用户 '+uidN;
  var pop=$('#userpop');
  // 统计先按「已知信息」画出来：POSTS 只是列表当前页，
  // 拿它算总帖数会严重偏低（一个发过几百帖的人可能只显示 1 帖）。
  // 真正的数字由 /api/user 给，拿到后再就地替换。
  var known=info.posts||0;
  pop.innerHTML=
    '<div class="urow2">'+avatarHtml(av,nm)+
      '<div style="min-width:0;flex:1">'+
        '<div class="un">'+esc(nm)+'</div>'+
        '<div class="um">MPID '+uidN+'</div>'+
      '</div></div>'+
    '<div class="ust" id="ustat">'+
      (known
        ? '<span>共 '+known+' 帖</span>'
        : '<span>统计中…</span>')+
    '</div>'+
    '<div class="uact">'+
      (IS_TOUCH?'<button class="ubtn go" data-a="go">看 TA 的帖子</button>':'')+
      '<button class="ubtn fl'+(isFollowed(uidN)?' on':'')+'" data-a="fl">'+
        (isFollowed(uidN)?'已关注':'关注')+'</button>'+
    '</div>';
  // 取该用户的真实统计（全库口径，不受当前页影响）
  api('api/user',{uid:uidN}).then(function(u){
    var el=document.getElementById('ustat');
    if(!el || pop._uid!==uidN || !u) return;
    el.innerHTML = u.posts
      ? '<span>共 '+u.posts+' 帖</span><span>获赞 '+u.likes+'</span>'+
        '<span>评论 '+u.comments+'</span>'
      : '<span>暂无发帖</span>'+
        (u.commentedOn? '<span>评论过 '+u.commentedOn+' 帖</span>'
                      : '<span>（只评论过）</span>');
  }).catch(function(){});
  pop.classList.add('on');
  // 贴在头像右侧；右边放不下就翻到左侧
  var r=anchorEl.getBoundingClientRect();
  var pw=pop.offsetWidth, ph=pop.offsetHeight;
  var left=r.right+10;
  if(left+pw>window.innerWidth-8) left=Math.max(8,r.left-pw-10);
  var top=r.top+(r.height-ph)/2;
  top=Math.max(8,Math.min(top,window.innerHeight-ph-8));
  pop.style.left=left+'px';
  pop.style.top=top+'px';
  pop._uid=uidN; pop._name=nm; pop._anchor=anchorEl;

  pop.querySelector('[data-a="fl"]').addEventListener('click',function(e){
    e.stopPropagation();
    toggleFollow(uidN,nm);
    // 只改按钮状态，不重进 showUserCard（重进曾与空头像一起造成死循环）
    var btn=e.currentTarget;
    var on=isFollowed(uidN);
    btn.classList.toggle('on', on);
    btn.textContent = on ? '已关注' : '关注';
    renderFollowBar();
  });
  // 触屏：点头像弹卡片，筛选跳转由这个按钮承担（桌面靠点头像本身）
  var goBtn=pop.querySelector('[data-a="go"]');
  if(goBtn) goBtn.addEventListener('click',function(e){
    e.stopPropagation();
    hideUserCard();
    filterByUser(uidN,nm);
    navClose();
  });
}
/* 分享：生成一条可粘贴到微北洋 App 的口令并复制。
   格式与 App 内「分享」一致（源码 post_detail_page.dart:818）。*/
function sharePost(id){
  var p=findPost(id);
  if(!p){ toast('帖子还没加载完，稍后再试'); return }
  var NL=String.fromCharCode(10);      // 换行符；直接写转义在多层模板里容易出错
  var txt='我在微北洋发现了个有趣的问题【'+(p.t||'')+'】'+NL+
          '#MP'+p.id+' ，你也来看看吧~'+NL+
          '将本条微口令复制到微北洋求实论坛打开问题 '+
          'wpy://school_project/'+p.id;
  copyText(txt).then(function(ok){
    toast(ok?'已复制分享口令':'复制失败，请手动复制');
  });
}

function hideUserCard(){
  var pop=$('#userpop');
  if(pop) pop.classList.remove('on');
}

document.addEventListener('click',function(e){
  // 点空白处收起浮层（触屏没有 mouseleave，必须靠点击外部关闭）
  var up=$('#userpop');
  if(up && up.classList.contains('on') &&
     !e.target.closest('#userpop') && !e.target.closest('[data-uid]')) hideUserCard();
  var fp=$('#followpop');
  if(fp && fp.classList.contains('on') &&
     !e.target.closest('#followpop') && !e.target.closest('#sw-follow')) fp.classList.remove('on');
  // 点昵称 -> 复制该帖 MP 号（分享口令）；点头像才是看该用户的全部帖子。
  // 放在 [data-uid] 分支之前：昵称同时带 data-uid 和 data-mp。
  var nmEl=e.target.closest('[data-mp]');
  if(nmEl){
    e.stopPropagation();
    var mp=nmEl.getAttribute('data-mp');
    copyText(mp).then(function(ok){
      toast(ok?('已复制 '+mp):'复制失败，请手动复制');
    });
    return;
  }
  // 点头像或昵称 -> 看该用户的全部帖子（触屏先弹用户卡片，可关注后再跳）。
  // 必须放在「点卡片打开详情」之前拦截，否则会被后者抢先处理。
  var who=e.target.closest('[data-uid]');
  if(who){
    e.stopPropagation();
    var uid=Number(who.getAttribute('data-uid'));
    var nmEl=who.classList.contains('nm')?who:who.parentNode.querySelector('.nm');
    if(IS_TOUCH){
      // 触屏没有 hover，用户卡片（含关注按钮）只能靠点击弹出；
      // 再点同一个头像就收起。跳转筛选走卡片里的「看 TA 的帖子」。
      var upp=$('#userpop');
      if(upp.classList.contains('on') && upp._uid===uid){ hideUserCard(); return; }
      hideUserCard();
      showUserCard(uid, who);
      return;
    }
    hideUserCard();
    // 先 filterByUser（它会从导航栈里读出「来源帖子」），
    // 再关弹窗——顺序反了的话栈已被清空，就记不住是从哪条帖子点进来的，
    // 退出筛选时就回不到原帖（踩过这个坑）。
    filterByUser(uid, nmEl?nmEl.textContent.trim():'');
    // 关掉详情弹窗，否则筛选结果在弹窗后面，看起来像"点了没反应"
    navClose();
    return;
  }
  // 点 MP 编号 -> 复制口令（App 里可直接粘贴打开该帖）
  var img=e.target.closest('img[data-full]');
  if(img){e.stopPropagation();$('#zimg').src=img.getAttribute('data-full');
    $('#zoom').classList.add('on');return}
  var card=e.target.closest('.card');
  if(card){openPost(Number(card.getAttribute('data-id')));return}
});
$('#zoom').addEventListener('click',function(){this.classList.remove('on');$('#zimg').src=''});
// 点弹窗外的灰色遮罩 -> 关掉弹窗（等同于回到列表）
$('#modal').addEventListener('click',function(e){
  if(e.target===this) navClose();
});
document.addEventListener('keydown',function(e){
  if(e.key!=='Escape') return;
  if($('#zoom').classList.contains('on')){$('#zoom').classList.remove('on');return}
  // Esc：栈里有上一层就退一层，否则关弹窗。
  // 这比「一律关闭」更符合直觉——看帖子时按 Esc 回到主页而不是回列表。
  if($('#modal').classList.contains('on')){
    if(navDepth()>1) navBack(); else navClose();
  }
});
/* 搜索防抖：输入时不要每敲一个字就查一次，
   停 320ms 后再查，既省请求也不卡。
   （原来用 input 事件直接查询，打「选课建议」会触发 4 次请求）*/
var _qTimer=null;
$('#q').addEventListener('input',function(){
  clearTimeout(_qTimer);
  // 输入中就给个"正在等"的细提示：不必等请求回来才知道系统收到了
  var b=document.getElementById('pbar');
  if(b) b.classList.add('on');
  _qTimer=setTimeout(function(){ applyFilter() },320);
});
$('#q').addEventListener('keydown',function(e){
  if(e.key==='Enter'){ clearTimeout(_qTimer); applyFilter() }
});
$('#so').addEventListener('change',function(){applyFilter()});
$('#yr') && $('#yr').addEventListener('change',applyFilter);
['mine','fav'].forEach(function(k){
  var e=$('#sw-'+k);
  if(e) e.addEventListener('click',function(){ e.classList.toggle('on'); applyFilter() });
});
// 关注开关：桌面点击直接切换筛选；触屏点开出浮层（里面再选看全部/看某人）
(function(){
  var e=$('#sw-follow');
  if(!e) return;
  e.addEventListener('click',function(){
    if(!IS_TOUCH){ e.classList.toggle('on'); applyFilter(); return; }
    if(!followCount()){
      toast('还没有关注的人；点用户头像即可关注'); return;
    }
    var pop=$('#followpop');
    if(pop.classList.contains('on')){ pop.classList.remove('on'); return; }
    pop.classList.add('on');
    var r=e.getBoundingClientRect();
    pop.style.left=Math.max(8,Math.min(r.left, window.innerWidth-pop.offsetWidth-8))+'px';
    pop.style.top=(r.bottom+7)+'px';
  });
})();
// 日期输入用 change 而不是 input：input 会在每敲一个数字时就发一次请求，
// 手动输入 2024-01-01 会连发十几次查询（而且中途的值都是无效日期）。
['#d1','#d2'].forEach(function(s){
  var e=$(s); if(e) e.addEventListener('change',applyFilter)});
// 分区标签：点击切换分区
document.querySelectorAll('.chip').forEach(function(c){
  c.addEventListener('click',function(e){
    if(c._dragMoved){ c._dragMoved=false; return }  // 拖完松手别误触切换
    CUR_TAB=c.getAttribute('data-tab')||'all';
    applyFilter();
  });
});
bindTabDrag();
$('#more').addEventListener('click',function(){loadPage(false)});
// 回到顶部按钮：滚过约两屏出现
var _backtop=$('#backtop');
if(_backtop) window.addEventListener('scroll',function(){
  _backtop.classList.toggle('on', window.scrollY>900);
},{passive:true});
/* ---- 左侧热榜栏：桌面端（≥1024px）常驻，窄屏不渲染 ----
   手机端保持原效果：热榜仍在独立页 /wpy/hot，点击跳帖子。
   桌面端点条目 → openPost 在右侧打开帖子，左栏不动；
   当前浏览的帖子在榜内高亮（由 renderTop 统一同步）。 */
var HOT_MQ=window.matchMedia('(min-width:1024px)');
var HOT_W='day', HOT_OPEN=null;
var HOT_DATA=Object.create(null);
function fmtHotNum(n){
  n=n||0;
  if(n>=10000) return (Math.round(n/1000)/10)+'万';
  return String(n);
}
function hotRender(){
  var host=$('#hotlist'); if(!host) return;
  var data=HOT_DATA[HOT_W]||[];
  host.innerHTML=data.map(function(p,i){
    var n=i+1;
    return '<li class="hotitem'+(String(p.id)===String(HOT_OPEN)?' on':'')
      +'" data-id="'+p.id+'">'
      +'<span class="hotrank'+(n<=3?' top':'')+'">'+n+'</span>'
      +'<span class="hotmain"><span class="hottitle">'+esc(p["标题"])+'</span>'
      +'<span class="hotmeta">'+fmtHotNum(p["浏览量"])+'浏览 · '
      +fmtHotNum(p["点赞数"])+'赞</span></span></li>';
  }).join('');
  var note=$('#hotnote');
  if(note){ note.style.display=data.length?'none':'block'; note.textContent='暂无数据'; }
}
function hotLoad(w,force){
  w=w||HOT_W;
  if(HOT_DATA[w] && !force){ hotRender(); return }
  var note=$('#hotnote');
  if(note){ note.style.display='block'; note.textContent='加载中…'; }
  api('/api/board-ranking',{board:'all',dim:'view',window:w,limit:20},force)
    .then(function(d){
      HOT_DATA[w]=d.data||[];
      hotRender();
    })
    .catch(function(){
      if(note){ note.style.display='block'; note.textContent='热榜暂时不可用'; }
    });
}
function setHotOpen(id){
  if(String(HOT_OPEN)===String(id||'')) return;
  HOT_OPEN=id||null;
  document.querySelectorAll('.hotitem').forEach(function(li){
    li.classList.toggle('on', li.getAttribute('data-id')===String(HOT_OPEN));
  });
}
function hotSyncVisible(){
  var on=HOT_MQ.matches;
  document.body.classList.toggle('hoton',on);
  if(on) hotLoad(HOT_W);
}
function initHot(){
  var aside=$('#hotside'); if(!aside) return;
  // 侧栏吸顶要避开 sticky 的头部；头部高度随内容变，量一次并在窗口变化时重算
  function hotOffset(){
    var h=document.querySelector('header');
    document.documentElement.style.setProperty('--headH',(h?h.offsetHeight:120)+'px');
  }
  hotOffset();
  window.addEventListener('resize',function(){
    hotOffset();
  },{passive:true});
  document.querySelectorAll('.hottabs .chipt').forEach(function(c){
    c.addEventListener('click',function(){
      document.querySelectorAll('.hottabs .chipt').forEach(function(x){ x.classList.remove('on') });
      c.classList.add('on');
      HOT_W=c.getAttribute('data-w')||'day';
      hotLoad(HOT_W);
    });
  });
  $('#hotrefresh').addEventListener('click',function(){ hotLoad(HOT_W,true) });
  $('#hotlist').addEventListener('click',function(e){
    var li=e.target.closest('.hotitem'); if(!li) return;
    openPost(parseInt(li.getAttribute('data-id'),10));
  });
  hotSyncVisible();
}
if(HOT_MQ.addEventListener) HOT_MQ.addEventListener('change',hotSyncVisible);

(function init(){
  applyTabOrder();
  collectNames();
  bootRole().then(function(){
    initYears();
    applyFilter();
    initHot();
  });
  bindMpHover();
  bindFollowHover();
  bindTabFade();
})();
