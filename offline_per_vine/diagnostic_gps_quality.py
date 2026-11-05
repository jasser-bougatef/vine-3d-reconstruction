#!/usr/bin/env python3
"""
diagnostic_gps_quality.py
Croise la qualité du fix GPS (colonne gps_Q) avec les plages de frames
assignées à chaque pied, pour détecter une dégradation localisée
(ex: multipath près des piquets métalliques) plutôt qu'un simple
décalage temporel global.

Usage :
    python3 diagnostic_gps_quality.py \
        --sync final_Output/Timesample_synchronised_files/synchronized_30-04-2025.csv \
        --jobs pied_jobs_summary.csv
"""
import argparse
import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", required=True, help="synchronized_DD-MM-YYYY.csv")
    ap.add_argument("--jobs", required=True, help="pied_jobs_summary.csv (généré par le pipeline patché)")
    args = ap.parse_args()

    sync = pd.read_csv(args.sync)
    jobs = pd.read_csv(args.jobs)

    print(f"{'Pied':<6}{'Pass':<7}{'Frames':>14}{'n':>5}"
          f"{'%Q=1(fix)':>12}{'%Q=2(float)':>13}{'%Q=5(single)':>13}{'Q dominant':>12}")
    print("─" * 90)

    suspects = []
    for _, j in jobs.iterrows():
        f_start, f_end = int(j["start_frame"]), int(j["end_frame"])
        sub = sync[(sync["frame_number"] >= f_start) & (sync["frame_number"] <= f_end)]
        if sub.empty or "gps_Q" not in sub.columns:
            continue
        n = len(sub)
        pct_fix    = 100 * (sub["gps_Q"] == 1).sum() / n
        pct_float  = 100 * (sub["gps_Q"] == 2).sum() / n
        pct_single = 100 * (sub["gps_Q"] == 5).sum() / n
        dominant   = sub["gps_Q"].mode().iloc[0] if not sub["gps_Q"].mode().empty else "?"

        flag = ""
        if pct_fix < 80:   # seuil à ajuster selon votre tolérance
            flag = "  <-- QUALITÉ DÉGRADÉE"
            suspects.append(int(j["pied_num"]))

        print(f"P{int(j['pied_num']):<5}{j['pass']:<7}{f'{f_start}-{f_end}':>14}{n:>5}"
              f"{pct_fix:>11.0f}%{pct_float:>12.0f}%{pct_single:>12.0f}%{str(dominant):>12}{flag}")

    print("\n" + "═" * 60)
    if suspects:
        print(f"Pieds avec qualité GPS dégradée (<80% de fix) : {sorted(set(suspects))}")
        print("→ Comparez cette liste à vos 5 pieds systématiquement mal sélectionnés.")
        print("→ Si ça correspond, le problème est un multipath GPS local, pas un offset.")
    else:
        print("Aucune dégradation de qualité détectée sur le seuil actuel (80%).")
        print("→ Baissez le seuil ou vérifiez la colonne 'reliable'/'time_gap_s' à la place.")


if __name__ == "__main__":
    main()