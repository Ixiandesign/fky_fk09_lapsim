import pymupdf, pymupdf4llm, pathlib, sys
out=pathlib.Path("ref/converted")
jobs={"ref/2023-Zacharelis-VehDynPerfSim (1).pdf":"zacharelis_2023_vehdyn_perfsim",
"ref/FSAE_2026_MI5_results.pdf":"fsae_2026_mi5_results",
"ref/FSAE_Rules_2027_V1.pdf":"fsae_rules_2027_v1"}
for src,name in jobs.items():
    doc=pymupdf.open(src)
    print(name,len(doc),"pages")
    kw={}
    if name.startswith("zach"):
        kw=dict(write_images=True,image_path=str(out/"zacharelis_paper_images"),image_format="png",dpi=110)
    chunks=pymupdf4llm.to_markdown(doc,page_chunks=True,**kw)
    with open(out/f"{name}.md","w",encoding="utf-8") as f:
        for c in chunks:
            p=c["metadata"].get("page_number",c["metadata"].get("page"))
            f.write(f"\n\n<!-- page {p} -->\n\n"+c["text"])
