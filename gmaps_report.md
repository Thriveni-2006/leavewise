# Pahunch vs Google Maps

Pairs compared: 13 of 13 (rest: no bus option found)

- Mean difference (typical vs Google): **17.6 min**; median 10.2 min
- Median difference as a share of Google's time: **18%**
- Google's time fell inside our range (typical -15% to plan-for): **54%** of pairs

```
         origin            dest depart  google  ours_typical  ours_plan           route  transfers
  Kashmere Gate     Nehru Place  08:30    61.0          58.5       95.4           425UP          0
Connaught Place           Saket  08:30    64.0          66.2      103.2         522DOWN          0
    Anand Vihar Connaught Place  08:30    40.0          63.8      100.8 39AUP → 352DOWN          1
         Rohini Connaught Place  08:30   100.0         132.1      186.9         164DOWN          0
   Lajpat Nagar      Karol Bagh  08:30    48.0          58.2       92.6         450DOWN          0
          AIIMS        Red Fort  08:30    69.0          56.8       86.4         502DOWN          0
      Hauz Khas     Anand Vihar  08:30    54.0         110.9      167.2 335UP → 39ADOWN          1
    Nehru Place Connaught Place  08:30    95.0          55.4       85.3         440DOWN          0
  Kashmere Gate    Lajpat Nagar  08:30    56.0          57.7       90.8           429UP          0
         Dwarka Connaught Place  08:30    77.0          82.9      126.0         752DOWN          0
          Saket   Kashmere Gate  08:30    69.0          69.2      111.0         419DOWN          0
     Karol Bagh     Nehru Place  08:30    57.0          53.0       87.5 966DOWN → 425UP          1
  Kashmere Gate     Nehru Place  23:00    99.0          61.2       95.3           425UP          0
```

Notes: Google was queried for a future departure; Google counts door-to-door including walking and its own wait. Pahunch's 'typical' is the median of observed bus behaviour, 'plan-for' is the 90th percentile. This is a sanity check, not proof; the held-out-day validation is the main test.