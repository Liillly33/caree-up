from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path
import re
import unicodedata

EMPTY={'','-','--','---','없음','미기재','자격요건미기재','우대사항미기재','원문정보소실','null','n/a','na','해당없음'}
STOP=set('a an the and or with in on of to for as at by from is are be have has ability skills skill experience experienced required preferred knowledge understanding strong good basic excellent work team teams teamwork communication communicate collaboration responsible responsibility personality motivated responsive driven willingness must can able using used use including etc developer development language framework based hands project projects relevant related degree university bachelor master major years year minimum more than 및 등 관련 기반 경험 이해 보유자 우대 가능자 업무 담당 수행 필수 자격 요건 사항 이상'.split())

def norm(text):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',text).strip()).casefold()

def sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:24]

def item_id(text):
    return hashlib.sha256(norm(text).encode()).hexdigest()[:24]

def empty(text):
    return re.sub(r'\s+','',str(text or '')).casefold() in EMPTY

def leaves(value):
    if value is None:return
    if isinstance(value,dict):
        for v in value.values():yield from leaves(v)
    elif isinstance(value,list):
        for v in value:yield from leaves(v)
    elif isinstance(value,str):
        text=value.strip()
        if empty(text):return
        if text[:1] in ('{','[','"'):
            try:parsed=json.loads(text)
            except ValueError:
                try:parsed=json.loads(text.replace('\\"','"'))
                except ValueError:
                    if text.startswith('{') or re.match(r'^\[\s*"',text):
                        decoder=json.JSONDecoder()
                        for match in re.finditer(r'(?<=[\[,])\s*(?=")',text):
                            try:v,end=decoder.raw_decode(text,match.end())
                            except ValueError:continue
                            if isinstance(v,str) and text[end:].lstrip()[:1] in (',',']'):yield from leaves(v)
                        return
                    parsed=None
            if isinstance(parsed,(dict,list)) or isinstance(parsed,str) and parsed!=value:
                yield from leaves(parsed);return
        yield text
    else:yield str(value)

def clauses(value):
    result=[];seen=set()
    for text in leaves(value):
        text=text.replace('\\r\\n','\n').replace('\\n','\n').replace('\r','\n')
        for line in re.split(r'[\n;•●▪ㆍ]+|(?<=\s)·(?=\s)',text):
            line=re.sub(r'^\s*(?:[-*·ㆍ?]+|\d+[.)])\s*','',line).strip()
            if empty(line) or re.fullmatch(r'\[[^\]]+\]\s*:?|[-—–]+',line):continue
            if norm(line) not in seen:
                seen.add(norm(line));result.append(line)
    return result

class TechTokenizer:
    def __init__(self,path):
        self.config=json.loads(Path(path).read_text(encoding='utf-8'))
        self.aliases={};self.categories={};self.scoring={}
        for entry in self.config['entries']:
            canonical=norm(entry['canonical']);self.categories[canonical]=entry['category'];self.scoring[canonical]=entry.get('score_enabled',True)
            for alias in entry['aliases']:
                alias=norm(alias)
                if alias in self.aliases and self.aliases[alias]!=canonical:raise ValueError('사전 별칭 중복: '+alias)
                self.aliases[alias]=canonical
        # Longest aliases win. Latin boundaries stop SQL matching inside MySQL,
        # Java matching inside JavaScript, and C matching inside C++ or C#.
        alternatives='|'.join(re.escape(a) for a in sorted(self.aliases,key=lambda a:(-len(a),a)))
        self.pattern=re.compile(r'(?<![a-z0-9_+#])(?:'+alternatives+r')(?![a-z0-9_+#])')
        self.version=sha(self.config)

    def analyze(self,text):
        text=norm(text);tokens=[];excluded=[];masked=list(text)
        for match in self.pattern.finditer(text):
            token=self.aliases[match.group()]
            if self.scoring[token]:tokens.append(token)
            else:excluded.append(token)
            masked[match.start():match.end()]=' '*(match.end()-match.start())
        unknown=sorted(set(t for t in re.findall(r'[a-z][a-z0-9]*(?:[.+/#_-][a-z0-9]+)*',''.join(masked)) if t not in STOP and len(t)>1))
        tokens=list(dict.fromkeys(tokens))
        return {'tokens':tokens,'explicit':sorted(set(tokens)),
                'projection':' '.join(tokens),'excluded_terms':sorted(set(excluded)),'unregistered_terms':unknown}

    def tokens(self,text):return self.analyze(text)['tokens']

def load_labels(path):
    with open(path,encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    result={}
    for row in rows:
        row['label']=row['label'].strip().upper()
        if row['label'] not in ('TECH','NONTECH','REVIEW'):continue
        if row['item_id']!=item_id(row['text']):raise ValueError('라벨 ID/원문 불일치')
        if row['item_id'] in result:raise ValueError('라벨 ID 중복')
        # match_text may be the user's own keyword summary, not a contiguous span.
        result[row['item_id']]=row
    return result
