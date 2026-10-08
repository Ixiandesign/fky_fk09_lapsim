import pdfplumber,csv,pathlib
pdf=pdfplumber.open("ref/FSAE_2026_MI5_results.pdf")
out=pathlib.Path("ref/converted/fsae_2026_results_csv")
fix=lambda s:(s or "").replace("\n"," ").replace("Universit\ufffd","Universit\u00e0").strip()
run4=lambda n:[f"r{i}_{c}" for i in range(1,5) for c in n]
H={
"overall":(range(1,6),"Place,CarNum,Team,Penalty,Cost,Presentation,Design,Acceleration,SkidPad,Autocross,Endurance,Efficiency,Total"),
"design":(range(6,10),"Place,CarNum,Team,DocumentPenalty,RawScore,LatePenalty,Status,Score"),
"presentation":(range(10,13),"Place,CarNum,Team,Finalist,RawScore,Penalty,Score"),
"cost":(range(13,18),"Place,CarNum,Team,AdjustedCost_USD,PriceScore_30,CostAccuracy_15,EngDrawings_15,Scenario_40,Penalty,Score"),
"acceleration":(range(18,23),"Place,CarNum,Team,"+",".join(run4(["Time","Cones","AdjTime"]))+",BestTime,Penalty,Score"),
"skidpad":(range(23,27),"Place,CarNum,Team,"+",".join(f"{d}_{c}" for d in ["D1R1","D1R2","D2R1","D2R2"] for c in ["TimeR","TimeL","Cones","AdjTime"])+",BestTime,Penalty,Score"),
"autocross":(range(27,31),"Place,CarNum,Team,"+",".join(run4(["Time","Cones","OffCourse","AdjTime"]))+",BestTime,Penalty,Score"),
"endurance":(range(31,35),"Place,CarNum,Team,Time,Laps,Cones,OffCourse,OtherPenalty,AdjustedTime,TimeScore,LapsScore,EnduranceScore"),
"efficiency":(range(35,38),"Place,CarNum,Team,AvgLaptime,LapsCompleted,FuelUsed_L,AdjCO2_kg,AvgAdjCO2PerLap_kg,FuelType,FuelEffFactor,FuelEffScore"),
"team_info":(range(41,44),"CarNum,Team,Country,EngineCylinders,EngineDisp_cc,Weight_kg,Weight_lbs"),
}
for name,(pages,hdr) in H.items():
    rows=[]
    for p in pages:
        t=pdf.pages[p-1].extract_tables()[0]
        for r in t:
            r=[fix(c) for c in r]
            if r[0] and not r[0].replace(" T","").replace("*","").strip().isdigit(): continue  # garbled header rows
            if not any(r): continue
            if name in("acceleration","skidpad","autocross") and not r[1]: continue
            rows.append(r)
    n=len(hdr.split(","))
    assert all(len(r)==n for r in rows),(name,{len(r) for r in rows},n)
    with open(out/f"{name}.csv","w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(hdr.split(",")); w.writerows(rows)
    print(name,len(rows))
# lap times
rows=[]
for p in (39,40):
    for r in pdf.pages[p-1].extract_tables()[0]:
        r=[fix(c) for c in r]
        if r[0]=="School" or not r[0]: continue
        rows.append([r[0],r[1]]+[c for c in r[2:] if c])
m=max(len(r) for r in rows)-2
with open(out/"endurance_laptimes.csv","w",newline="",encoding="utf-8") as f:
    w=csv.writer(f); w.writerow(["Team","CarNum"]+[f"Lap{i}" for i in range(1,m+1)]); w.writerows(rows)
print("laptimes",len(rows),m)
