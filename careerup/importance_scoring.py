"""Independent, auditable importance coverage; unreviewed terms stay neutral."""
def importance_coverage(terms,query,labels,idf,reference_count,budget,weights,evidence_power=1.0):
    groups={'core':set(),'auxiliary':set(),'neutral':set()}
    for token in terms:
        kind={'핵심':'core','보조':'auxiliary'}.get(labels.get(token),'neutral')
        groups[kind].add(token)
    present={k:weights[k] for k,tokens in groups.items() if tokens}
    total=sum(present.values())
    effective={k:budget*present.get(k,0)/total if total else 0.0 for k in groups}
    matched=terms & query
    # A tiny posting cannot earn full coverage merely by matching its one term.
    # This corpus reference never depends on the number of user skills.
    sufficiency=min(1.0,len(matched)/reference_count)**evidence_power if reference_count else 0.0
    scores={};details={}
    for kind,tokens in groups.items():
        mass=sum(idf[t] for t in tokens)
        matched_tokens=tokens & query
        ratio=sum(idf[t] for t in matched_tokens)/mass if mass else 0.0
        scores[kind]=ratio*sufficiency
        details[kind]={'technologies':sorted(tokens),'matched_technologies':sorted(matched_tokens),
                       'coverage':ratio,'evidence_adjusted_coverage':scores[kind],
                       'effective_weight':effective[kind]}
    contributions={k:100*effective[k]*scores[k] for k in groups}
    return contributions,{'groups':details,'evidence_sufficiency':sufficiency,
                          'reference_technology_count':reference_count,
                          'neutral_policy':'unreviewed_or_undetermined_not_auxiliary'}
