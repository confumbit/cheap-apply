```latex
% -----------EXPERIENCE-----------
\section{Experience}

\resumeSubHeadingListStart

\resumeSubheading
{Full-Stack Developer}{Mar. 2023 -- Present}
{Freelance \& Fractal}{Remote}
\resumeItemListStart
\resumeItem{Delivered 10+ production web and mobile applications for client businesses via Upwork and Fractal, specializing in Flutter cross-platform architecture, RESTful APIs, and payment gateway integration.}

\resumeItem{\textbf{Order Procurement \& Management Solution} | \emph{Flutter, NextJS, ExpressJS, PostgreSQL, Stripe}}
\resumeItemListStart
\resumeItem{Built a full-stack order management portal using NextJS, ExpressJS, and PostgreSQL with session authentication, Dio HTTP handlers, and Stripe payment integration.}
\resumeItem{Designed a dual-tier interface with a customer ordering portal for live product listings and an administrative suite for task dispatch, audit logging, and recurring orders.}
\resumeItem{Automated order ingestion by parsing incoming WhatsApp client messages into structured PostgreSQL records via regex rules, eliminating manual entry and increasing processing speed.}
\resumeItem{Structured relational PostgreSQL schemas with foreign key constraints and B-tree indexes to optimize transaction throughput across customer and administrative order tables.}
\resumeItemListEnd
\resumeItemListEnd

\medskip

\resumeSubheading
{Flutter Developer}{Oct. 2023 -- Apr. 2025}
{Tagglabs Experimental Pvt. Ltd. (Contract)}{Gurugram, Haryana (Remote)}
\resumeItemListStart
\resumeItem{\textbf{Cross-platform AR App} | \emph{Flutter, Dart, BLoC, Clean Architecture, OpenCV, Django} \textbf{(\underline{\href{[suspicious link removed]}{Live link}})}}
\resumeItemListStart
\resumeItem{Architected a native C/C++ bridge using \texttt{dart:ffi} to port OpenCV SIFT algorithms into Flutter, structuring codebase via Clean Architecture for iOS and Android (Kotlin) platforms.}
\resumeItem{Engineered a real-time camera feed pipeline normalizing OS frame buffers and aspect ratios, converting video streams to C structs with zero memory leaks at 30+ FPS.}
\resumeItem{Implemented BLoC state management to stream match coordinates asynchronously from C algorithms to Dart, driving dynamic AR UI overlay rendering with sub-50ms latency.}
\resumeItem{Constructed responsive Flutter interfaces from Figma specs and connected 15+ REST APIs in Django using Dio, delivering location-aware spatial discovery features.}
\resumeItemListEnd

\medskip

\resumeItem{\textbf{Kia National Conference App} | \emph{Flutter, Dart, Firebase, BLoC}}
\resumeItemListStart
\resumeItem{Deployed a mobile app in Flutter for 500+ attendees at the Kia Bangkok Conference, integrating Firebase Push Notifications, BLoC state management, and Mockito unit test coverage.}
\resumeItemListEnd
\resumeItemListEnd

\resumeSubHeadingListEnd

% -----------TECHNICAL SKILLS-----------
\section{Technical Skills}
\begin{itemize}[leftmargin=0.15in, label={}]
  \small{\item{
        \textbf{Mobile \& Frameworks:} {Flutter, Dart, BLoC, Provider, GetX, Clean Architecture, MVVM, React Native, NextJS, Django} \\
        \textbf{Backend \& Testing:} {RESTful APIs, Dio, Firebase (Auth, Firestore, Crashlytics), Mockito, PostgreSQL, NodeJS, ExpressJS} \\
        \textbf{Languages \& Vision:} {Dart, Python, JavaScript/TypeScript, C/C++, Kotlin, Swift, SQL, OpenCV (SIFT)} \\
        \textbf{Developer Tools \& Cloud:} {Git, GitHub, JIRA, Figma, Google Play Store, App Store, Docker, Linux, AWS, \BeginAccSupp{ActualText=LaTeX}\LaTeX\EndAccSupp{}} \\
        }}
\end{itemize}
```

### Report

**Title:**
- Role 1: Kept *Full-Stack Developer* (reflects web, mobile, and e-commerce engineering across projects).
- Role 2: Retitled *Software Developer* to *Flutter Developer* (aligns directly with target role family and Tagglabs' cross-platform Flutter focus).

**Keywords:**
- ✅ Covered: Flutter, Dart, BLoC, Clean Architecture, RESTful APIs, Firebase (Push Notifications, Auth, Crashlytics), Dio, Mockito, Google Play Store, App Store, Git, JIRA, Kotlin, Swift, Django, Stripe, Figma (placed in experience bullets, project headings, and skills).
- ❌ Skipped: Razorpay, PayU, PayPal, Freshdesk, Trello (omitted to focus on core Flutter, Firebase, BLoC, and Clean Architecture requirements without keyword stuffing).

**Interview Prep:**
- *Dio HTTP & Stripe integration (Order Portal):* Walk through configuring Dio interceptors and Stripe checkout session handling.
- *Clean Architecture & Kotlin FFI bridge (DiscoverLoka):* Detail decoupling FFI C-bindings inside repository layers and handling Kotlin camera stream buffers.
- *sub-50ms latency (Estimate 1):* Explain frame-by-frame SIFT keypoint extraction timing over Dart isolates.
- *15+ REST APIs via Dio (Estimate 2):* Detail Django REST endpoints serving spatial keypoints, user profiles, and location queries.
- *Firebase Push Notifications & Mockito (Kia App):* Explain Mockito unit testing for BLoC state streams and FCM payload handlers.

**Length Check:**
- Visible Characters: Original = 2,744 | New = 2,682 (62 characters under budget; 0 line overruns).
- Bullet Counts: Original = 10 | New = 10 (Role 1: 5, Role 2: 5).