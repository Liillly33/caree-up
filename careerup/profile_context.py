"""Keep explicit negative/planned statements out of positive skill evidence.
No activity matching, seniority or experience-level score is calculated.
"""
import re
from text_processing import norm, clauses

UNCERTAIN = re.compile(
    r'(?:경험|해본\s*적|사용해본\s*적).{0,12}(?:없|않)|미경험|못했|못함|'
    r'예정|계획|목표|배우고\s*싶|해보고\s*싶|희망|준비\s*중|학습\s*중|공부\s*중|'
    r'(?:하지|해보지|사용하지|수행하지|한\s*적이)\s*않|안\s*(?:했|함|해봄)|'
    r'\b(?:no\s+experience|never|not\s+(?:deployed|implemented|used|operated)|plan\s+to|planning|want\s+to|hope\s+to)\b'
)

def positive_profile_fragments(values,tokenizer):
    records=[];ignored=[]
    for value in values:
        for clause in clauses(value):
            for part in re.split(r'[,，]|[.!?](?=\s|$)|하지만|그러나|반면',clause):
                text=part.strip()
                if not text:continue
                tokens=tokenizer.tokens(text)
                if not tokens:continue
                if UNCERTAIN.search(norm(text)):ignored.append(text);continue
                records.append({'text':text,'technologies':sorted(set(tokens))})
    return {'records':records,'ignored':ignored}
