# Genuine call script: bank EMI reminder (should stay GREEN)

> Read aloud to show low false positives.

1. "Namaste, main HDFC Bank se Priya bol rahi hoon, kya meri baat Sharma ji se ho rahi hai?"
2. "Sir, aapki home loan ki EMI is mahine paanch tarikh ko due hai, amount hai baarah hazaar chaar sau rupaye."
3. "Kripya due date se pehle account mein balance rakh dijiye ya bank ke app se pay kar dijiye."
4. "Aur haan sir, bank kabhi bhi phone par OTP ya PIN nahi maangta, kisi ko mat dijiyega."
5. "Koi sawal ho to card ke peeche wale customer care number par call kar sakte hain. Dhanyavaad, aapka din shubh ho."

**Expected:** the score stays under ~30, with no takeover. Line 4 mentions "OTP" and "mat dijiyega" in a *protective* way,
which is a good test that the AI understands context better than keywords alone.

# Family call (should stay GREEN)

1. "Haan papa, main pahunch gaya Pune. Train thodi late thi."
2. "Khana kha liya? Dawai time pe le lena, main Sunday ko video call karunga."
