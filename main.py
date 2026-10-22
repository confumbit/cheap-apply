import os, sys, logging

from dotenv import load_dotenv

from job_functions import classify_jobs, scraper, scrape_hr
from resume_pipeline import tailor_jobs

load_dotenv()

logger = logging.getLogger(__name__)

def run_workflow() -> None:
    logger.info("Workflow started")

    logger.info("Scraping jobs started")
    path = scraper()
    logger.info("Scraping jobs finished")

    logger.info("Classifying jobs according to experience started")
    classify_jobs(path)
    logger.info("Classifying jobs according to experience finished")

    logger.info("Tailoring resumes for eligible jobs started")
    tailor_jobs(path, experience_column="experience_range")
    logger.info("Tailoring resumes for eligible jobs finished")

    # logger.info("Finding HR emails started")
    # scrape_hr()
    # logger.info("Finding HR emails finished")

    logger.info("Workflow completed")




def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        run_workflow()
    except Exception:
        logger.exception("Workflow failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
