#!/bin/zsh
# เช็คที่ราบด่าน Pullback (PULLBACK_MIN_DISAGREE_BARS · ระบบจริง 6) — 0 / 4 / 8 / 12 แท่ง ทั้งสองช่วง (2026-10-22)
# ใช้: nohup tools/run_pb_plateau.sh > results/plateau_logs/status.log 2>&1 &
# ผล: results/replay_trades_<sym>_c1022P{1016,2324}_reva_pbdis{0,4,8,12}.csv · log ราย symbol ที่ results/plateau_logs/
cd "$(dirname "$0")/.."
L=results/plateau_logs
SYMS=(BTCUSDm XAUUSDm ETHUSDm USDJPYm US500m EURUSDm UKOILm HK50m GBPCHFm AUDNZDm)
run() {  # $1 days $2 end $3 tag $4 bars $5 name
  for s in $SYMS; do ( ./run_wine.sh tools/backtest_replay.py $s $1 --end=$2 --tag=$3 --rev-a --pb-min-disagree=$4 > $L/$5_$s.log 2>&1 ) & done; wait
  echo "$5 done $(date) · auth fail: $(grep -l 'Authorization failed' $L/$5_*.log 2>/dev/null | wc -l | tr -d ' ') · regime error: $(cat $L/$5_*.log | grep -c 'regime error')"
}
for n in 0 4 8 12; do run 300 2024-09-24T00:00 c1022P2324 $n pb${n}_23; done
for n in 0 4 8 12; do run 730 2026-09-24T00:00 c1022P1016 $n pb${n}_24; done
echo DONE $(date)
