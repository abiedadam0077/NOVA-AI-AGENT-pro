# NOVA AI AGENT (pro)

NOVA AI Agent — تحكّم في وكيل ذكاء اصطناعي يعدّل مستودعات GitHub الحقيقية:
ترسل أمراً، الوكيل يقرأ الملفات ويعدّلها ويشغّل الاختبارات والبناء داخل
GitHub Actions، يصلح أخطاءه بنفسه، ثم تحصل على النتائج الحقيقية (ملخص + سجل
الأدوات + diff للمراجعة + artifacts للتحميل).

المعمارية الأساسية: **GitHub App (OAuth/PKCE) + GitHub Actions + OpenRouter**.

## مكونات المشروع

| المكوّن | المسار | الحالة |
|---|---|---|
| نواة الوكيل (agent loop + أدوات + أمان) | `agent/nova_agent/` | ✅ منفَّذة ومختبَرة (pytest) |
| واجهة سطر الأوامر | `agent/run_agent.py` | ✅ تعمل محلياً (mock) وفي CI |
| Workflow تشغيل الوكيل | `.github/workflows/nova-agent.yml` | ✅ جاهز — يحتاج secret ‏`OPENROUTER_API_KEY` |
| CI لاختبارات النواة | `.github/workflows/ci.yml` | ✅ يعمل مع كل push |
| واجهة المستخدم (ربط GitHub، اختيار المستودع، إرسال الأوامر، عرض النتائج) | — | ⏳ بانتظار ملف المواصفات الرسمي |

## كيف يعمل مسار الوكيل

1. تشغّل **Actions → NOVA Agent → Run workflow** وتكتب الأمر
   (يدوياً الآن، ومن التطبيق لاحقاً عبر `workflow_dispatch` API).
2. الوكيل يستدعي **OpenRouter** في حلقة: يفحص الملفات → يعدّلها →
   يشغّل أوامر حقيقية (اختبارات/بناء) → يصلح الأخطاء → `finish` بملخص صادق.
3. التغييرات تُنشر في branch جديد + **Pull Request** للمراجعة،
   والنتائج (result.json + diff.patch) تُرفع كـ **artifacts**،
   وملخص التنفيذ يظهر في صفحة الـ run.

## الأمان

- مفاتيح API تُخزن فقط في **GitHub Actions Secrets** — لا تُوضع في الكود أبداً.
- أوامر الوكيل تعمل في بيئة منزوعة المفاتيح الحساسة (`OPENROUTER_API_KEY`،
  `GITHUB_TOKEN` …) مع قواعد منع للأوامر التدميرية وسجن المسارات داخل المستودع.
- كل تغيير يمر عبر PR — لا شيء يدمج إلى `main` تلقائياً.

## التشغيل المحلي للاختبار (بدون مفتاح)

```bash
python3 agent/run_agent.py \
  --command "Create hello.py that prints hello" \
  --workspace /tmp/nova-ws \
  --mock-script tests-data/mock_script.json \
  --result-json /tmp/nova-ws/result.json
```

## الاختبارات

```bash
python3 -m pytest agent/tests -v
```
